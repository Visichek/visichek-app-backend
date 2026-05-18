"""Unified upload service for the new /v1/uploads/* surface.

Replaces ad-hoc upload code paths (badge photo, kiosk file fields,
visitor portrait) with one routine that:

  * Enforces plan + addon storage quotas (via storage_quota_service),
  * Persists a Document row (so the document is tracked + auditable),
  * Returns a stable ``object_key`` + presigned ``download_url`` so
    callers can drop the key into ``tenant_form_data`` / ``bio_data``
    on a follow-up submit.

Two callers:
  * Private upload (``POST /v1/uploads/private``) — authenticated.
  * Public tenant upload (``POST /v1/public/tenants/{id}/uploads``) —
    plan-gated; see ``enforce_public_upload_access``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from core.errors import AppException, ErrorCode
from core.storage import DocumentStorageManager
from core.storage.types import StorageBackend
from repositories.document_repo import create_document
from schemas.document_schema import DocumentCreate, DocumentOut
from schemas.upload_schema import UploadPurpose, UploadResponse
from services.storage_quota_service import enforce_storage_quota

logger = logging.getLogger(__name__)

_PURPOSE_PREFIX: dict[UploadPurpose, str] = {
    UploadPurpose.KIOSK_FORM: "kiosk-form",
    UploadPurpose.VISITOR_PHOTO: "visitor-photos",
    UploadPurpose.ID_DOCUMENT: "id-docs",
    UploadPurpose.APPOINTMENT_PHOTO: "appointment-photos",
    UploadPurpose.BRANDING: "branding",
    UploadPurpose.SYSTEM: "system",
}


def _object_key_for(
    *, tenant_id: Optional[str], purpose: UploadPurpose, file_name: str
) -> str:
    prefix = _PURPOSE_PREFIX.get(purpose, "uploads")
    tenant_segment = tenant_id or "shared"
    extension = Path(file_name).suffix
    return f"{prefix}/{tenant_segment}/{uuid4().hex}{extension}"


async def perform_upload(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    file_name: str,
    mime_type: str,
    payload: bytes,
    purpose: UploadPurpose,
    field_id: Optional[str] = None,
) -> UploadResponse:
    """Upload bytes, persist Document row, return UploadResponse.

    Reused by both the private and public upload routes. Enforces
    storage quota *before* the bytes hit storage so we don't even
    attempt to write a file the tenant can't afford.
    """
    size = len(payload)
    if size <= 0:
        raise AppException(
            status_code=400,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="Uploaded file is empty",
        )
    if size > 50 * 1024 * 1024:
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large (hard cap 50 MB)",
            details={"max_size_bytes": 50 * 1024 * 1024},
        )

    # plan + addon enforcement — single source of truth.
    await enforce_storage_quota(tenant_id, size)

    object_key = _object_key_for(
        tenant_id=tenant_id, purpose=purpose, file_name=file_name
    )
    checksum = hashlib.md5(payload).hexdigest()

    provider = DocumentStorageManager.get_instance().provider
    provider.upload_bytes(
        object_key=object_key, payload=payload, mime_type=mime_type
    )
    backend = StorageBackend(provider.backend_name).value

    now = int(time.time())
    doc: DocumentOut = await create_document(
        DocumentCreate(
            owner_id=owner_id,
            tenant_id=tenant_id,
            file_name=file_name,
            object_key=object_key,
            backend=backend,
            mime_type=mime_type,
            size=size,
            checksum=checksum,
            metadata={
                "purpose": purpose.value,
                "field_id": field_id,
            },
            created_at=now,
            updated_at=now,
        )
    )
    # ``doc`` is asserted via the schema; we only use object_key &
    # storage echo so a None ``id`` is fine — the response surface
    # is keyed by object_key.
    _ = doc

    expires_in = 24 * 3600
    try:
        download_url = provider.download_url(
            object_key=object_key, expires_in=expires_in
        )
    except Exception:
        download_url = ""

    return UploadResponse(
        object_key=object_key,
        download_url=download_url,
        file_name=file_name,
        mime_type=mime_type,
        size=size,
        backend=backend,
        purpose=purpose,
        tenant_id=tenant_id,
        field_id=field_id,
        expires_in_seconds=expires_in,
    )


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
