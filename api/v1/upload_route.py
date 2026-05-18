"""Unified upload API.

Two endpoints share the same upload pipeline:

  * ``POST /v1/uploads/private`` — authenticated, all plans incl Free.
    Used for system flows (badge photos, ID re-scans, etc.) and any
    upload where the caller already has a session.

  * ``POST /v1/public/tenants/{tenant_id}/uploads`` — plan-gated.
    Used to back ``file`` / ``image`` / ``signature`` / ``id_document``
    fields on kiosk forms. Denied on plans that don't enable
    ``/v1/public/tenants/*/uploads``; in that case a system user with
    visitor permissions (super_admin / dept_admin / receptionist)
    must drive the call instead.

Both return a stable ``object_key`` the caller drops into the matching
``tenant_form_data`` / ``bio_data`` slot on the subsequent submit.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from core.response_envelope import document_response
from schemas.upload_schema import UploadPurpose, UploadResponse
from security.auth import verify_any_token, verify_optional_kiosk_token
from security.principal import AuthPrincipal
from services.storage_quota_service import get_storage_quota
from services.upload_service import (
    enforce_public_upload_access,
    perform_upload,
)

router = APIRouter(prefix="/uploads", tags=["Uploads"])
public_router = APIRouter(prefix="/public/tenants", tags=["Uploads (Public)"])


@router.post("/private", status_code=status.HTTP_201_CREATED)
@document_response(
    message="File uploaded",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Authenticated upload available on every plan (including Free). "
        "Use for system flows (badge photos, receptionist-captured ID "
        "scans, host avatars) and anywhere the caller already holds a "
        "valid session. The returned ``object_key`` is what gets stored "
        "on the parent record (visitor.portrait_object_key, etc.).\n\n"
        "Counts against the tenant's storage quota when the principal "
        "is tenant-scoped; app admins / users without a tenant bypass "
        "the per-tenant quota."
    ),
    summary="Private (authenticated) upload",
    response_codes={
        400: "Empty / invalid file",
        401: "Unauthorized — bearer token required",
        413: "File exceeds plan max_file_size_mb or hard 50 MB cap",
        429: "Storage quota or document-count cap reached",
    },
)
async def private_upload_endpoint(
    file: UploadFile = File(...),
    purpose: UploadPurpose = Form(default=UploadPurpose.SYSTEM),
    field_id: Optional[str] = Form(default=None),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> UploadResponse:
    payload = await file.read()
    return await perform_upload(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        file_name=file.filename or "upload",
        mime_type=file.content_type or "application/octet-stream",
        payload=payload,
        purpose=purpose,
        field_id=field_id,
    )


@public_router.post("/{tenant_id}/uploads", status_code=status.HTTP_201_CREATED)
@document_response(
    message="File uploaded",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public kiosk upload used by ``file`` / ``image`` / "
        "``signature`` / ``id_document`` fields on a published "
        "TenantForm. The returned ``object_key`` should be dropped into "
        "the matching ``tenant_form_data`` / ``bio_data`` slot on the "
        "subsequent submit so the form validator sees the upload was "
        "supplied.\n\n"
        "**Plan-gated** — anonymous access is allowed only when the "
        "tenant's plan grants ``/v1/public/tenants/*/uploads``. On Free "
        "/ Starter the kiosk must attach a Bearer token belonging to a "
        "super_admin / dept_admin / receptionist of the same tenant."
    ),
    summary="Public (plan-gated) kiosk upload",
    response_codes={
        400: "Empty / invalid file",
        403: "Plan denies public uploads and no valid system user token was presented",
        413: "File exceeds plan max_file_size_mb or hard 50 MB cap",
        429: "Storage quota or document-count cap reached",
    },
)
async def public_tenant_upload_endpoint(
    tenant_id: str,
    file: UploadFile = File(...),
    purpose: UploadPurpose = Form(default=UploadPurpose.KIOSK_FORM),
    field_id: Optional[str] = Form(
        default=None,
        description="Tenant form field_id this upload satisfies",
    ),
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> UploadResponse:
    await enforce_public_upload_access(tenant_id=tenant_id, principal=principal)
    payload = await file.read()
    return await perform_upload(
        owner_id=(principal.user_id if principal else "public_kiosk"),
        tenant_id=tenant_id,
        file_name=file.filename or "upload",
        mime_type=file.content_type or "application/octet-stream",
        payload=payload,
        purpose=purpose,
        field_id=field_id,
    )


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
