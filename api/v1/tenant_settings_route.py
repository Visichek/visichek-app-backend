from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.tenant_settings_schema import TenantSettingsOut, TenantSettingsUpdate
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_settings_service import (
    retrieve_or_create_tenant_settings,
    update_tenant_settings_by_id,
)

router = APIRouter(prefix="/tenants", tags=["Tenant Settings"])


@router.get("/{tenant_id}/settings")
@document_response(
    message="Tenant settings fetched successfully",
    success_example={
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_name": "Acme Corp",
        "default_timezone": "Africa/Lagos",
        "enforce_totp": False,
        "password_min_length": 8,
        "max_failed_login_attempts": 5,
        "lockout_duration_minutes": 30,
        "session_timeout_minutes": 60,
        "require_id_scan": False,
        "require_consent_before_check_in": True,
        "visitor_data_retention_days": 365,
        "send_welcome_email": True,
    },
    description=(
        "Get the organization-level settings for a tenant. "
        "Creates defaults on first access (upsert pattern). "
        "Only super_admin can read."
    ),
    summary="Get tenant settings",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - must be super admin",
    },
)
async def get_tenant_settings(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Get tenant settings. Creates defaults on first access."""
    return await retrieve_or_create_tenant_settings(tenant_id)


@router.patch("/{tenant_id}/settings")
@document_response(
    message="Tenant settings updated successfully",
    description=(
        "Partial update of organization-level settings. Only send fields that changed. "
        "Security policies are enforced server-side. Only super_admin can write."
    ),
    summary="Update tenant settings",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
        422: "Validation error",
    },
)
async def update_tenant_settings(
    tenant_id: str,
    data: TenantSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Update tenant settings."""
    return await update_tenant_settings_by_id(
        tenant_id,
        data,
        actor_id=principal.user_id,
        actor_role=principal.role,
    )
