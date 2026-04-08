from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.tenant_settings_schema import TenantSettingsUpdate
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_settings_service import (
    retrieve_or_create_tenant_settings,
    update_tenant_settings_by_id,
)

router = APIRouter(prefix="/tenant-settings", tags=["Tenant Settings (Unified)"])


@router.get("")
@document_response(
    message="Tenant settings fetched successfully",
    success_example={
        "security": {
            "enforceTotp": False,
            "passwordMinLength": 8,
            "sessionTimeoutMinutes": 60,
            "maxFailedLoginAttempts": 5,
        },
        "visitors": {
            "requireIdScan": False,
            "requireHostApproval": False,
            "requireConsent": True,
            "allowSelfRegistration": True,
        },
    },
    description=(
        "Return the tenant's settings. Tenant ID is inferred from the super admin's token. "
        "Auto-creates defaults on first access. Only super_admin can read."
    ),
    summary="Get tenant settings",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
    },
)
async def get_tenant_settings(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Get tenant settings — tenant ID inferred from token."""
    tenant_id = principal.tenant_id or ""
    return await retrieve_or_create_tenant_settings(tenant_id)


@router.patch("")
@document_response(
    message="Tenant settings updated successfully",
    description=(
        "Partial update of tenant settings. Accepts any subset of fields. "
        "Tenant ID is inferred from the super admin's token. Only super_admin can write."
    ),
    summary="Update tenant settings",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
        422: "Validation error",
    },
)
async def update_tenant_settings(
    data: TenantSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Update tenant settings — tenant ID inferred from token."""
    tenant_id = principal.tenant_id or ""
    return await update_tenant_settings_by_id(
        tenant_id,
        data,
        actor_id=principal.user_id,
        actor_role=principal.role,
    )
