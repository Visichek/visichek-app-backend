"""Unified, presigned-only upload service for the /v1/uploads/* surface.

Every client upload — private and public — uses the SAME two-step,
presigned flow. The server never receives file bytes:

  1. ``create_upload_intent`` validates the request (image-MIME guard,
     advisory storage-quota pre-check), reserves a stable ``object_key``,
     writes a *pending* Document row, and returns a presigned ``upload_url``.
  2. The client PUTs the raw bytes straight to ``upload_url`` (S3, or the
     local presign-shim endpoint in dev).
  3. ``confirm_upload`` HEADs the object for the *authoritative* size /
     content-type, enforces storage quota for real, flips the Document row
     to *ready*, and returns the ``object_key`` + a fresh ``download_url``.

The returned ``object_key`` is what callers drop into ``tenant_form_data`` /
``bio_data`` / ``visitor.portrait_object_key`` etc. on the follow-up submit.

Server-GENERATED artifacts (invoice PDFs, composed badges, ID crops) do NOT
use this module — they push bytes directly via ``provider.upload_bytes`` since
there is no client to presign for.

Two callers each for both steps:
  * Private  (``/v1/uploads/intent`` + ``/confirm``) — authenticated.
  * Public   (``/v1/public/tenants/{id}/uploads/intent`` + ``/confirm``) —
    plan-gated; see ``enforce_public_upload_access``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from core.errors import AppException, ErrorCode, resource_not_found
from core.storage import DocumentStorageManager
from core.storage.types import StorageBackend
from repositories.document_repo import (
    PENDING_STATUS,
    create_document,
    delete_document,
    get_document_by_key,
    list_stale_pending_documents,
    mark_document_ready,
)
from schemas.document_schema import DocumentCreate
from schemas.upload_schema import (
    UploadIntentResponse,
    UploadPurpose,
    UploadResponse,
)
from services.storage_quota_service import enforce_storage_quota

logger = logging.getLogger(__name__)

# Hard ceiling regardless of plan — mirrors the legacy multipart cap.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# How long a presigned PUT URL is valid for.
_INTENT_TTL_SECONDS = 3600

# How long a download (GET) URL is presigned for by default.
_DOWNLOAD_TTL_SECONDS = 24 * 3600

# Pending rows older than this (intent issued, never confirmed) are swept by
# ``cleanup_pending_uploads``. Comfortably longer than the intent TTL so an
# in-flight upload is never reaped mid-flight.
_PENDING_MAX_AGE_SECONDS = 6 * 3600

_PURPOSE_PREFIX: dict[UploadPurpose, str] = {
    UploadPurpose.KIOSK_FORM: "kiosk-form",
    UploadPurpose.VISITOR_PHOTO: "visitor-photos",
    UploadPurpose.ID_DOCUMENT: "id-docs",
    UploadPurpose.APPOINTMENT_PHOTO: "appointment-photos",
    UploadPurpose.BRANDING: "branding",
    UploadPurpose.SYSTEM: "system",
    UploadPurpose.HOST_PHOTO: "host-photos",
    UploadPurpose.HOST_SIGNATURE: "host-signatures",
}

# Purposes that MUST be raster images. Scoped intentionally to the host
# roster fields so generic buckets (kiosk ``file`` / ``id_document`` that
# legitimately carry PDFs, ``system`` catch-all) keep accepting any type.
_IMAGE_ONLY_PURPOSES: frozenset[UploadPurpose] = frozenset(
    {UploadPurpose.HOST_PHOTO, UploadPurpose.HOST_SIGNATURE}
)

# SVG is deliberately excluded — it can carry inline script and these slots
# render straight into an <img>/badge, so an attacker-supplied SVG would be a
# stored-XSS vector.
_ALLOWED_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/heic",
        "image/heif",
    }
)


def _normalize_mime(mime_type: str) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


def _enforce_image_mime(purpose: UploadPurpose, mime_type: str) -> None:
    """Reject a non-image upload for an image-only purpose (HTTP 415).

    Runs at BOTH steps: against the client-declared type at intent (precise
    UX error before a presigned URL is minted) and against the storage-read
    content type at confirm (defence in depth — the client can't smuggle a
    different type past the signed Content-Type). No-op for other purposes.
    """
    if purpose not in _IMAGE_ONLY_PURPOSES:
        return
    normalized = _normalize_mime(mime_type)
    # Local storage records no content type; ``None`` slips through here and is
    # re-checked against the declared type, which was already validated.
    if not normalized or normalized in _ALLOWED_IMAGE_MIME_TYPES:
        return
    raise AppException(
        status_code=415,
        code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        message=(
            "Unsupported file type for an image field. Upload a JPEG, PNG, "
            "WebP, GIF, BMP, or HEIC/HEIF image."
        ),
        details={
            "received_mime_type": normalized or None,
            "allowed_mime_types": sorted(_ALLOWED_IMAGE_MIME_TYPES),
            "purpose": purpose.value,
        },
    )


def _object_key_for(
    *, tenant_id: Optional[str], purpose: UploadPurpose, file_name: str
) -> str:
    prefix = _PURPOSE_PREFIX.get(purpose, "uploads")
    tenant_segment = tenant_id or "shared"
    extension = Path(file_name).suffix
    return f"{prefix}/{tenant_segment}/{uuid4().hex}{extension}"


# ─── Step 1: intent ─────────────────────────────────────────────────


async def create_upload_intent(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    file_name: str,
    mime_type: str,
    declared_size: int,
    purpose: UploadPurpose,
    field_id: Optional[str] = None,
) -> UploadIntentResponse:
    """Reserve an object_key + presigned PUT URL for a client upload.

    The declared ``mime_type`` / ``declared_size`` are advisory: they drive
    the image-MIME guard and an early quota pre-check so the client gets a
    fast 4xx instead of uploading bytes that confirm would reject. The
    authoritative checks run in :func:`confirm_upload`.
    """
    if declared_size <= 0:
        raise AppException(
            status_code=400,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="Declared file size must be greater than zero",
        )
    if declared_size > _MAX_UPLOAD_BYTES:
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large (hard cap 50 MB)",
            details={"max_size_bytes": _MAX_UPLOAD_BYTES},
        )

    _enforce_image_mime(purpose, mime_type)

    # Advisory pre-check on the declared size — fail fast before minting a URL.
    await enforce_storage_quota(tenant_id, declared_size)

    object_key = _object_key_for(
        tenant_id=tenant_id, purpose=purpose, file_name=file_name
    )

    provider = DocumentStorageManager.get_instance().provider
    intent = provider.presign_put(
        object_key=object_key,
        mime_type=mime_type,
        expires_in=_INTENT_TTL_SECONDS,
    )
    backend = StorageBackend(provider.backend_name).value

    # Reserve a PENDING document row. size=0 so it never inflates quota until
    # confirmed; the cleanup job sweeps it if confirm never arrives.
    now = int(time.time())
    await create_document(
        DocumentCreate(
            owner_id=owner_id,
            tenant_id=tenant_id,
            file_name=file_name,
            object_key=object_key,
            backend=backend,
            mime_type=mime_type,
            size=0,
            checksum=None,
            status=PENDING_STATUS,
            metadata={"purpose": purpose.value, "field_id": field_id},
            created_at=now,
            updated_at=now,
        )
    )

    return UploadIntentResponse(
        object_key=intent.object_key,
        upload_url=intent.upload_url,
        method=intent.method,
        headers=intent.headers,
        expires_in=intent.expires_in,
        backend=backend,
        purpose=purpose,
        field_id=field_id,
    )


# ─── Step 2: confirm ────────────────────────────────────────────────


async def confirm_upload(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    object_key: str,
) -> UploadResponse:
    """Finalise an upload after the client PUT the bytes to storage.

    HEADs the object for the real size/type, enforces storage quota
    authoritatively (deleting the object + rejecting if over budget), and
    flips the pending Document row to ready.
    """
    doc = await get_document_by_key(object_key)
    if doc is None:
        raise resource_not_found("Upload intent", object_key)

    # Ownership / tenant scoping — a caller may only confirm their own intent.
    if doc.owner_id != owner_id:
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="This upload was not initiated by the caller.",
        )
    if (doc.tenant_id or None) != (tenant_id or None):
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="Upload tenant scope mismatch.",
        )

    provider = DocumentStorageManager.get_instance().provider
    info = provider.head_object(object_key=object_key)
    if info is None:
        raise AppException(
            status_code=409,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message=(
                "No uploaded object found for this key. PUT the file to the "
                "upload_url before confirming."
            ),
            details={"object_key": object_key},
        )

    real_size = info.size
    # Local storage records no content type — fall back to the declared one.
    real_mime = _normalize_mime(info.content_type or "") or doc.mime_type

    if real_size <= 0:
        await _discard(provider, object_key, doc.id)
        raise AppException(
            status_code=400,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="Uploaded object is empty.",
        )
    if real_size > _MAX_UPLOAD_BYTES:
        await _discard(provider, object_key, doc.id)
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large (hard cap 50 MB).",
            details={"max_size_bytes": _MAX_UPLOAD_BYTES},
        )

    purpose = _purpose_of(doc.metadata)
    try:
        _enforce_image_mime(purpose, real_mime)
        # Authoritative quota enforcement on the REAL size.
        await enforce_storage_quota(tenant_id, real_size)
    except Exception:
        await _discard(provider, object_key, doc.id)
        raise

    updated = await mark_document_ready(
        object_key=object_key,
        size=real_size,
        mime_type=real_mime,
        checksum=None,
    )
    if updated is None:
        # Lost the row between the read and the update (concurrent confirm /
        # cleanup). Treat as gone rather than serving a half-built response.
        raise resource_not_found("Upload intent", object_key)

    download_url = _safe_download_url(provider, object_key, _DOWNLOAD_TTL_SECONDS)

    return UploadResponse(
        object_key=object_key,
        download_url=download_url,
        file_name=updated.file_name,
        mime_type=real_mime,
        size=real_size,
        backend=updated.backend,
        purpose=purpose,
        tenant_id=tenant_id,
        field_id=(updated.metadata or {}).get("field_id"),
        expires_in_seconds=_DOWNLOAD_TTL_SECONDS,
    )


def _safe_download_url(provider, object_key: str, expires_in: int) -> str:
    try:
        return provider.download_url(object_key=object_key, expires_in=expires_in)
    except Exception:
        return ""


def _purpose_of(metadata: Optional[dict]) -> UploadPurpose:
    raw = (metadata or {}).get("purpose")
    try:
        return UploadPurpose(raw)
    except (ValueError, TypeError):
        return UploadPurpose.SYSTEM


async def _discard(provider, object_key: str, document_id: Optional[str]) -> None:
    """Best-effort rollback: drop the stored object + the pending row."""
    try:
        provider.delete_object(object_key=object_key)
    except Exception:
        logger.warning("Failed to delete rejected object %s", object_key)
    if document_id:
        try:
            await delete_document(document_id=document_id)
        except Exception:
            logger.warning("Failed to delete pending doc row %s", document_id)


# ─── Pending-upload cleanup (APScheduler) ───────────────────────────


async def cleanup_pending_uploads() -> dict[str, int]:
    """Sweep pending Document rows whose intent was never confirmed.

    Deletes the orphaned storage object (if the client uploaded but never
    confirmed) and the reservation row. Scheduled hourly via APScheduler —
    referenced textually so it stays picklable (gotcha #3).
    """
    cutoff = int(time.time()) - _PENDING_MAX_AGE_SECONDS
    stale = await list_stale_pending_documents(cutoff)
    provider = DocumentStorageManager.get_instance().provider
    removed = 0
    for doc in stale:
        try:
            provider.delete_object(object_key=doc.object_key)
        except Exception:
            pass  # object may never have been uploaded — row deletion still proceeds
        if doc.id and await delete_document(document_id=doc.id):
            removed += 1
    if removed:
        logger.info("cleanup_pending_uploads removed %d stale pending uploads", removed)
    return {"scanned": len(stale), "removed": removed}


# ─── Public-upload plan gate ────────────────────────────────────────


async def enforce_public_upload_access(
    *, tenant_id: str, principal: Optional[object]
) -> None:
    """Apply the same gate the public submit uses.

    On plans that grant ``/v1/public/tenants/*/uploads`` the route is
    anonymous; on Free / Starter a system user with visitor
    permissions (super_admin / dept_admin / receptionist) is
    required. Mirrors :func:`services.checkin_config_service.enforce_kiosk_submit_access`.
    """
    from services.plan_limits import is_feature_enabled

    try:
        allowed = await is_feature_enabled(
            tenant_id=tenant_id,
            endpoint_pattern="/v1/public/tenants/*/uploads",
            method="POST",
        )
    except Exception:
        allowed = True  # fail-open — auth path still runs below

    if allowed:
        return

    if principal is None:
        raise AppException(
            status_code=403,
            code=ErrorCode.FEATURE_DISABLED,
            message=(
                "This tenant's plan does not include public kiosk file "
                "uploads. Log in as a receptionist / department admin / "
                "super admin and resubmit, or upgrade the plan to enable "
                "anonymous kiosk uploads."
            ),
            details={"required": "system_user_with_visitor_permissions"},
        )

    role = getattr(principal, "role", "")
    principal_tenant_id = getattr(principal, "tenant_id", None)
    if role not in ("super_admin", "dept_admin", "receptionist"):
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message=(
                "Public kiosk uploads are disabled on this plan and only "
                "tenant receptionists, department admins, or super admins "
                "may drive the kiosk."
            ),
            details={"role": role},
        )
    if principal_tenant_id and principal_tenant_id != tenant_id:
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="System user is not scoped to this tenant.",
            details={
                "tenant_id": tenant_id,
                "principal_tenant_id": principal_tenant_id,
            },
        )
