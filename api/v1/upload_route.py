"""Unified, presigned-only upload API.

Every client upload uses the same two-step flow and never streams bytes
through the server:

  1. ``POST .../intent``   → returns a presigned ``upload_url``.
  2. client PUTs the raw file bytes straight to ``upload_url``.
  3. ``POST .../confirm``  → finalises the record, returns ``object_key`` +
     a fresh ``download_url``.

Two surfaces share the same pipeline:

  * ``/v1/uploads/intent`` + ``/v1/uploads/confirm`` — authenticated, all
    plans incl. Free. System flows (badge photos, ID re-scans, host avatars).
  * ``/v1/public/tenants/{tenant_id}/uploads/intent`` + ``/confirm`` —
    plan-gated kiosk uploads backing ``file`` / ``image`` / ``signature`` /
    ``id_document`` form fields. Denied on plans that don't grant
    ``/v1/public/tenants/*/uploads``; in that case a super_admin / dept_admin
    / receptionist token of the same tenant must drive the call.

``GET /v1/uploads/url`` mints a fresh presigned download URL for an object the
caller owns (or that belongs to their tenant).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from core.errors import auth_permission_denied, resource_not_found
from core.response_envelope import document_response
from repositories.document_repo import get_document_by_key
from schemas.upload_schema import (
    UploadConfirmRequest,
    UploadIntentRequest,
    UploadIntentResponse,
    UploadResponse,
)
from security.auth import verify_any_token, verify_optional_kiosk_token
from security.principal import AuthPrincipal
from services.storage_quota_service import get_storage_quota
from services.storage_url_service import resolve_download_url
from services.upload_service import (
    confirm_upload,
    create_upload_intent,
    enforce_public_upload_access,
)

router = APIRouter(prefix="/uploads", tags=["Uploads"])
public_router = APIRouter(prefix="/public/tenants", tags=["Uploads (Public)"])

_PUBLIC_KIOSK_OWNER = "public_kiosk"


# ─── Private (authenticated) upload ─────────────────────────────────


@router.post("/intent", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Upload intent created",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Step 1 of an authenticated upload (every plan incl. Free). Declares "
        "the file and returns a presigned ``upload_url``. PUT the raw bytes to "
        "that URL using the returned ``method`` + ``headers`` (S3 requires the "
        "signed ``Content-Type``), then call ``POST /v1/uploads/confirm`` with "
        "the returned ``object_key``.\n\n"
        "The declared ``size`` / ``mime_type`` are advisory (fast pre-check); "
        "the authoritative quota + MIME checks run at confirm. App admins / "
        "users without a tenant bypass the per-tenant storage quota."
    ),
    summary="Private upload — step 1 (intent)",
    response_codes={
        400: "Empty / invalid declared size",
        401: "Unauthorized — bearer token required",
        413: "Declared size exceeds plan max_file_size_mb or hard 50 MB cap",
        415: "Non-image file for an image-only purpose (host_photo / host_signature)",
        429: "Storage quota or document-count cap reached",
    },
)
async def private_upload_intent_endpoint(
    payload: UploadIntentRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> UploadIntentResponse:
    return await create_upload_intent(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        declared_size=payload.size,
        purpose=payload.purpose,
        field_id=payload.field_id,
    )


@router.post("/confirm", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Upload confirmed",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Step 2 of an authenticated upload. Call after PUTting the bytes to the "
        "``upload_url`` from step 1. The server reads the real size/type back "
        "from storage, enforces storage quota authoritatively, finalises the "
        "document record, and returns the stable ``object_key`` (drop it onto "
        "the parent record) plus a fresh ``download_url``."
    ),
    summary="Private upload — step 2 (confirm)",
    response_codes={
        400: "Uploaded object empty",
        401: "Unauthorized — bearer token required",
        403: "Upload was not initiated by the caller / tenant mismatch",
        409: "No uploaded object found for the key (PUT the bytes first)",
        413: "Uploaded object exceeds 50 MB cap",
        415: "Non-image file for an image-only purpose",
        429: "Storage quota or document-count cap reached",
    },
)
async def private_upload_confirm_endpoint(
    payload: UploadConfirmRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> UploadResponse:
    return await confirm_upload(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        object_key=payload.object_key,
    )


# ─── Public (plan-gated) kiosk upload ───────────────────────────────


@public_router.post("/{tenant_id}/uploads/intent", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Upload intent created",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Step 1 of a public kiosk upload backing ``file`` / ``image`` / "
        "``signature`` / ``id_document`` fields on a published TenantForm. "
        "Returns a presigned ``upload_url``; PUT the bytes there, then confirm. "
        "Drop the returned ``object_key`` into the matching ``tenant_form_data`` "
        "/ ``bio_data`` slot on the subsequent submit.\n\n"
        "**Plan-gated** — anonymous access only when the tenant's plan grants "
        "``/v1/public/tenants/*/uploads``. Otherwise attach a Bearer token for a "
        "super_admin / dept_admin / receptionist of the same tenant."
    ),
    summary="Public kiosk upload — step 1 (intent)",
    response_codes={
        400: "Empty / invalid declared size",
        403: "Plan denies public uploads and no valid system user token was presented",
        413: "Declared size exceeds plan max_file_size_mb or hard 50 MB cap",
        415: "Non-image file for an image-only purpose",
        429: "Storage quota or document-count cap reached",
    },
)
async def public_tenant_upload_intent_endpoint(
    tenant_id: str,
    payload: UploadIntentRequest,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> UploadIntentResponse:
    await enforce_public_upload_access(tenant_id=tenant_id, principal=principal)
    return await create_upload_intent(
        owner_id=(principal.user_id if principal else _PUBLIC_KIOSK_OWNER),
        tenant_id=tenant_id,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        declared_size=payload.size,
        purpose=payload.purpose,
        field_id=payload.field_id,
    )


@public_router.post("/{tenant_id}/uploads/confirm", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Upload confirmed",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Step 2 of a public kiosk upload. Call after PUTting the bytes to the "
        "``upload_url`` from step 1. Same plan gate as the intent step. Returns "
        "the stable ``object_key`` for the kiosk submit."
    ),
    summary="Public kiosk upload — step 2 (confirm)",
    response_codes={
        400: "Uploaded object empty",
        403: "Plan denies public uploads / tenant mismatch",
        409: "No uploaded object found for the key (PUT the bytes first)",
        413: "Uploaded object exceeds 50 MB cap",
        415: "Non-image file for an image-only purpose",
        429: "Storage quota or document-count cap reached",
    },
)
async def public_tenant_upload_confirm_endpoint(
    tenant_id: str,
    payload: UploadConfirmRequest,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> UploadResponse:
    await enforce_public_upload_access(tenant_id=tenant_id, principal=principal)
    return await confirm_upload(
        owner_id=(principal.user_id if principal else _PUBLIC_KIOSK_OWNER),
        tenant_id=tenant_id,
        object_key=payload.object_key,
    )


# ─── Download URL retrieval ─────────────────────────────────────────


@router.get("/url")
@document_response(
    message="Download URL minted",
    description=(
        "Mint a fresh presigned download URL for an object the caller owns "
        "(or that belongs to the caller's tenant). Use this whenever a stored "
        "``download_url`` may have expired rather than caching the URL itself. "
        "Returns ``{ object_key, download_url, expires_in_seconds }``."
    ),
    summary="Get a fresh download URL for an object_key",
    response_codes={
        401: "Unauthorized — bearer token required",
        403: "Object does not belong to the caller / tenant",
        404: "No document found for the object_key",
    },
)
async def get_download_url_endpoint(
    object_key: str = Query(..., min_length=1),
    expires_in: int = Query(default=24 * 3600, ge=60, le=7 * 24 * 3600),
    principal: AuthPrincipal = Depends(verify_any_token),
):
    doc = await get_document_by_key(object_key)
    if doc is None:
        raise resource_not_found("Document", object_key)

    owns = doc.owner_id == principal.user_id
    same_tenant = bool(principal.tenant_id) and doc.tenant_id == principal.tenant_id
    if not (owns or same_tenant or principal.is_admin):
        raise auth_permission_denied("GET:/v1/uploads/url")

    return {
        "object_key": object_key,
        "download_url": resolve_download_url(object_key, expires_in=expires_in),
        "expires_in_seconds": expires_in,
    }


# ─── Storage quota read ─────────────────────────────────────────────


storage_router = APIRouter(prefix="/storage", tags=["Storage"])


@storage_router.get("/quota")
@document_response(
    message="Storage quota retrieved",
    description=(
        "Compute the tenant's current storage budget. Returns the plan "
        "allowance, summed active addons, used bytes, and remaining "
        "headroom. Use as the read source for any 'you've used X of Y' "
        "UI before kicking off an upload."
    ),
    summary="Get tenant storage quota",
)
async def get_storage_quota_endpoint(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        # App admins / users have no per-tenant budget — return zeros.
        return {
            "tenant_id": "",
            "plan_storage_mb": None,
            "addon_storage_mb": 0,
            "total_storage_mb": None,
            "used_bytes": 0,
            "used_mb": 0.0,
            "remaining_mb": None,
            "document_count": 0,
            "max_documents": None,
            "max_file_size_mb": 10,
            "active_addons": 0,
        }
    return await get_storage_quota(tenant_id)
