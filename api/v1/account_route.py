from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.session_schema import AccountDeleteRequest
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.account_deletion_service import (
    delete_admin_account,
    delete_system_user_account,
)

router = APIRouter(prefix="/account", tags=["Account"])


@router.delete("")
@document_response(
    message="Account scheduled for deletion",
    success_example={"deleted": True},
    description=(
        "Request account deletion. Requires password confirmation. "
        "Revokes all sessions, marks the account as INACTIVE. "
        "For super admins: returns 403 if they are the sole super admin "
        "of a tenant or if the tenant has an active subscription."
    ),
    summary="Delete account",
    response_codes={
        401: "Unauthorized — invalid password",
        403: "Forbidden — sole super admin or tenant has active subscription",
        404: "Account not found",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid password",
            "code": "AUTH_INVALID_CREDENTIALS",
        },
        403: {
            "success": False,
            "message": "Cannot delete the sole super admin of a tenant",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def delete_account(
    data: AccountDeleteRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Delete the authenticated user's account."""
    if principal.role in TENANT_USER_ROLES:
        await delete_system_user_account(
            user_id=principal.user_id,
            password=data.password,
            tenant_id=principal.tenant_id or "",
        )
    else:
        await delete_admin_account(
            admin_id=principal.user_id,
            password=data.password,
        )
    return {"deleted": True}
