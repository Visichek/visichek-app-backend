from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.branding_schema import BrandingUpdate
from security.auth import verify_super_admin_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.branding_service import (
    upsert_branding,
    retrieve_branding_by_tenant,
    retrieve_public_branding_by_tenant,
    remove_branding,
)

router = APIRouter(prefix="/branding", tags=["Tenant Branding"])


# ─── Public (unauthenticated) ───────────────────────────────────────────


@router.get("/public/tenant/{tenant_id}")
@document_response(
    message="Branding fetched successfully",
    success_example={
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_display_name": "Acme Corp",
        "primary_color": "#1A73E8",
        "secondary_color": "#34A853",
        "accent_color": "#FBBC04",
        "logo_url": "https://s3.../tenants/64f1a.../logo.png?X-Amz-...",
        "favicon_url": "https://s3.../tenants/64f1a.../favicon.ico?X-Amz-...",
    },
    description=(
        "Public endpoint — no authentication required. "
        "Returns a subset of branding fields needed to render the tenant "
        "login screen (colors, display name, logo, favicon). "
        "Badge-specific fields and internal object keys are omitted."
    ),
    summary="Get tenant branding (public)",
    response_codes={
        404: "Not found - tenant has no branding configured",
    },
    error_examples={
        404: {"success": False, "message": "Branding not found for this tenant", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def get_public_tenant_branding(tenant_id: str):
    """Public branding — for login screens and unauthenticated contexts."""
    return await retrieve_public_branding_by_tenant(tenant_id)


# ─── Authenticated (system user) ────────────────────────────────────────


@router.get("/tenant/{tenant_id}")
@document_response(
    message="Branding fetched successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d9",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_display_name": "Acme Corp",
        "primary_color": "#1A73E8",
        "secondary_color": "#34A853",
        "accent_color": "#FBBC04",
        "badge_header_color": "#1A73E8",
        "badge_text_color": "#FFFFFF",
        "logo_object_key": "tenants/64f1a.../logo.png",
        "favicon_object_key": "tenants/64f1a.../favicon.ico",
        "badge_logo_position": "top_center",
        "logo_url": "https://s3.../tenants/64f1a.../logo.png?X-Amz-...",
        "favicon_url": "https://s3.../tenants/64f1a.../favicon.ico?X-Amz-...",
        "date_created": 1712500000,
        "last_updated": 1712500600,
    },
    description=(
        "Retrieve the branding configuration for a tenant. "
        "Returns null data if no branding has been configured yet. "
        "Logo and favicon URLs are presigned and expire after a short period."
    ),
    summary="Get tenant branding",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def get_tenant_branding(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Retrieve branding for a tenant. Any authenticated system user can read."""
    return await retrieve_branding_by_tenant(tenant_id)


@router.put("")
@document_response(
    message="Branding updated successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d9",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_display_name": "Acme Corp",
        "primary_color": "#1A73E8",
        "secondary_color": "#34A853",
        "accent_color": "#FBBC04",
        "badge_header_color": "#1A73E8",
        "badge_text_color": "#FFFFFF",
        "logo_object_key": "tenants/64f1a.../logo.png",
        "favicon_object_key": None,
        "badge_logo_position": "top_center",
        "logo_url": "https://s3.../tenants/64f1a.../logo.png?X-Amz-...",
        "favicon_url": None,
        "date_created": 1712500000,
        "last_updated": 1712501200,
    },
    description=(
        "Create or update the branding configuration for the super admin's tenant. "
        "Uses upsert semantics — if no branding exists, one is created; otherwise "
        "the existing config is partially updated with the provided fields. "
        "Only super admins can modify branding."
    ),
    summary="Set tenant branding",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - must be super admin",
        422: "Validation error - invalid color format or input data",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "primary_color must be a valid hex color", "code": "VALIDATION_FAILED"},
    },
)
async def set_tenant_branding(
    branding_data: BrandingUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Create or update branding for the super admin's tenant."""
    tenant_id = principal.tenant_id or ""
    return await upsert_branding(tenant_id=tenant_id, branding_data=branding_data)


@router.delete("")
@document_response(
    message="Branding reset to defaults",
    success_example={"deleted": True},
    description=(
        "Delete the branding configuration for the super admin's tenant, "
        "resetting it to platform defaults. Only super admins can do this."
    ),
    summary="Reset tenant branding",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - must be super admin",
        404: "Not found - no branding configured for this tenant",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        404: {"success": False, "message": "Branding not found for this tenant", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def reset_tenant_branding(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Delete branding config, resetting to defaults."""
    tenant_id = principal.tenant_id or ""
    await remove_branding(tenant_id=tenant_id)
    return {"deleted": True}
