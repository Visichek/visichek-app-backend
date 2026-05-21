from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from core.errors import auth_invalid_token
from core.response_envelope import document_response
from schemas.imports import UserType
from schemas.admin_schema import AdminRefresh
from schemas.session_schema import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordSendRequest,
    ResetPasswordWithTokenRequest,
    TwoFactorVerifyRequest,
    TwoFactorDisableRequest,
    BackupCodesRegenerateRequest,
)
from schemas.system_user_schema import SystemUserRefresh
from schemas.user_schema import UserRefresh
from security.auth import verify_any_refresh_token, verify_any_token
from security.cookie_utils import REFRESH_TOKEN_COOKIE, build_auth_response
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.admin_service import refresh_admin_tokens_reduce_number_of_logins
from services.password_change_service import (
    change_admin_password,
    change_system_user_password,
)
from services.system_user_service import refresh_system_user_tokens
from services.forgot_password_service import (
    lookup_reset_accounts,
    reset_password_with_token,
    send_reset_for_selection,
)
from services.two_factor_service import (
    setup_two_factor,
    verify_two_factor_setup,
    disable_two_factor_with_password,
    regenerate_backup_codes_with_password,
)
from services.user_service import refresh_user_tokens_reduce_number_of_logins


class UnifiedRefreshRequest(BaseModel):
    """Role-agnostic refresh request body.

    `refresh_token` is optional in the body — if omitted, the unified endpoint
    falls back to reading it from the `refresh_token` cookie.
    """

    refresh_token: Optional[str] = Field(default=None)


router = APIRouter(prefix="/auth", tags=["Auth Management"])


def _user_type(principal: AuthPrincipal) -> UserType:
    return (
        UserType.SYSTEM_USER if principal.role in TENANT_USER_ROLES else UserType.ADMIN
    )


async def _get_email(principal: AuthPrincipal) -> str:
    """Resolve the user's email from the database."""
    from core.database import db
    from bson import ObjectId

    user_type = _user_type(principal)
    collection = "admins" if user_type == UserType.ADMIN else "system_users"
    doc = await db[collection].find_one(
        {"_id": ObjectId(principal.user_id)}, {"email": 1}
    )
    return doc["email"] if doc else "user@visichek.com"


# ═══════════════════════════════════════════════════════════════════
# PASSWORD CHANGE
# ═══════════════════════════════════════════════════════════════════


@router.post("/change-password")
@document_response(
    message="Password changed successfully",
    success_example={"changed": True},
    description=(
        "Change the authenticated user's password. Validates current password, "
        "enforces password policy (min 8 chars, uppercase, lowercase, digit, "
        "special char, not in breached list, no sequential/repeated chars), "
        "and checks password history (last 5). Works for both admins and system users."
    ),
    summary="Change password",
    response_codes={
        401: "Unauthorized — current password is wrong",
        422: "Validation error — new password doesn't meet policy or was recently used",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Current password is incorrect",
            "code": "AUTH_INVALID_CREDENTIALS",
        },
        422: {
            "success": False,
            "message": "Password does not meet strength requirements",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def change_password(
    data: ChangePasswordRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Change password for the authenticated user."""
    if _user_type(principal) == UserType.ADMIN:
        await change_admin_password(
            principal.user_id, data.current_password, data.new_password
        )
    else:
        await change_system_user_password(
            principal.user_id, data.current_password, data.new_password
        )
    return {"changed": True}


# ═══════════════════════════════════════════════════════════════════
# FORGOT PASSWORD (unauthenticated)
# ═══════════════════════════════════════════════════════════════════


@router.post("/forgot-password")
@document_response(
    message="Account lookup complete.",
    success_example={
        "selectionToken": "x9aF…opaque",
        "expiresIn": 900,
        "accounts": [
            {
                "accountRef": "k3l…opaque",
                "type": "platform",
                "label": "Platform Administrator",
                "email": "jane@acme.com",
                "tenantId": None,
                "tenantName": None,
                "role": "admin",
                "roleLabel": "Platform Administrator",
            },
            {
                "accountRef": "p7q…opaque",
                "type": "tenant",
                "label": "Acme Corp",
                "email": "jane@acme.com",
                "tenantId": "665…",
                "tenantName": "Acme Corp",
                "role": "super_admin",
                "roleLabel": "Tenant Super Admin",
            },
        ],
    },
    description=(
        "Step 1 of the two-step forgot-password flow. Unauthenticated. "
        "Submit an email address; the server returns EVERY account that "
        "shares it — at most one platform admin plus one entry per tenant "
        "the email belongs to — each with display fields (type, label, "
        "tenant name, role) so the frontend can render an account picker. "
        "NO email is sent at this step.\n\n"
        "The response also carries an opaque, short-lived (15 min), "
        "single-use ``selectionToken``. The frontend echoes it back to "
        "POST /v1/auth/forgot-password/send together with the "
        "``accountRef`` value(s) the user picked; only then are reset "
        "links emailed.\n\n"
        "When nothing matches, ``accounts`` is empty (a selection token is "
        "still returned so the response shape is uniform). The env-pinned "
        "primary admin is excluded — its recovery uses OTP_DEV_CODE + env "
        "credentials."
    ),
    summary="Step 1 — look up accounts for an email",
)
async def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
):
    """Look up accounts that share an email (no email sent)."""
    return await lookup_reset_accounts(email=data.email, request=request)


@router.post("/forgot-password/send", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Reset link(s) sent for the selected account(s).",
    status_code=status.HTTP_202_ACCEPTED,
    success_example={"sent": 1},
    description=(
        "Step 2 of the two-step forgot-password flow. Unauthenticated. "
        "Submit the ``selectionToken`` from step 1 plus the ``accountRefs`` "
        "the user selected. The server mints a single-use reset link per "
        "chosen account and emails it (HTML template ``password_reset`` — "
        "a button that opens the frontend reset page at "
        "``{APP_BASE_URL}/reset-password?token=…``; no raw token is shown "
        "to the user).\n\n"
        "Refs that were not part of the original selection are ignored. "
        "The selection is single-use and expires 15 minutes after step 1, "
        "so a stale or replayed token returns 400. The actual reset is "
        "completed by POST /v1/auth/reset-password."
    ),
    summary="Step 2 — send reset link(s) for selected account(s)",
    response_codes={
        400: "Invalid, expired, or already-used selection token",
    },
)
async def forgot_password_send(
    data: ForgotPasswordSendRequest,
    request: Request,
):
    """Email reset link(s) for the account(s) the user selected."""
    return await send_reset_for_selection(
        selection_token=data.selection_token,
        account_refs=data.account_refs,
        request=request,
    )


@router.post("/reset-password")
@document_response(
    message="Password reset successfully",
    success_example={"reset": True},
    description=(
        "Unauthenticated. Submit the token from the password-reset email "
        "together with the new password. Enforces the same password policy "
        "and history rules as POST /v1/auth/change-password. Side effects: "
        "every active access/refresh token for the account is revoked "
        "(forcing re-login on every device), every other outstanding "
        "reset token for the account is invalidated, and the gate cache "
        "snapshot is dropped so the next request reads fresh state."
    ),
    summary="Consume reset token + set new password",
    response_codes={
        400: "Invalid, used, expired, or malformed token",
        403: "Forbidden — the env primary admin cannot be reset via this flow",
        404: "Account not found",
        422: "Password fails strength / history check",
    },
)
async def reset_password(
    data: ResetPasswordWithTokenRequest,
    request: Request,
):
    """Consume a reset token and set a new password."""
    await reset_password_with_token(
        token=data.token, new_password=data.new_password, request=request
    )
    return {"reset": True}


# ═══════════════════════════════════════════════════════════════════
# TWO-FACTOR AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════


@router.post("/2fa/setup")
@document_response(
    message="2FA setup initiated",
    status_code=status.HTTP_201_CREATED,
    success_example={
        "secret": "JBSWY3DPEHPK3PXP",
        "otpauthUri": "otpauth://totp/VisiChek:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=VisiChek",
        "qrCodeUri": "otpauth://totp/VisiChek:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=VisiChek",
    },
    description=(
        "Generate a TOTP secret and provisioning URI for the authenticator app. "
        "The 2FA is NOT active until verified with POST /v1/auth/2fa/verify. "
        "The frontend should render the QR code from the otpauthUri field."
    ),
    summary="Setup 2FA",
    response_codes={
        401: "Unauthorized",
        409: "Conflict — 2FA is already enabled",
    },
)
async def setup_2fa(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Initiate TOTP 2FA setup."""
    user_type = _user_type(principal)
    email = await _get_email(principal)
    result = await setup_two_factor(principal.user_id, user_type, email)
    # Return both field names for compatibility
    return {
        "secret": result["secret"],
        "otpauth_uri": result["qr_code_uri"],
        "qr_code_uri": result["qr_code_uri"],
    }


@router.post("/2fa/verify")
@document_response(
    message="2FA verified and enabled",
    success_example={
        "enabled": True,
        "backupCodes": ["ABC12345", "DEF67890", "GHI13579", "JKL24680", "MNO11223"],
    },
    description=(
        "Verify a TOTP code to complete 2FA enrollment. On success, returns backup codes "
        "that the user should save securely — they are shown only once and cannot be retrieved later."
    ),
    summary="Verify 2FA setup",
    response_codes={
        401: "Unauthorized — invalid TOTP code",
        404: "No pending 2FA setup found",
    },
)
async def verify_2fa(
    data: TwoFactorVerifyRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Verify TOTP code to activate 2FA. Returns backup codes."""
    user_type = _user_type(principal)
    backup_codes = await verify_two_factor_setup(
        principal.user_id, user_type, data.code
    )
    return {"enabled": True, "backup_codes": backup_codes}


@router.delete("/2fa")
@document_response(
    message="2FA disabled",
    success_example={"disabled": True},
    description="Disable 2FA. Requires current password for confirmation.",
    summary="Disable 2FA",
    response_codes={
        401: "Unauthorized — invalid password",
        404: "2FA is not currently enabled",
    },
)
async def disable_2fa(
    data: TwoFactorDisableRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Disable 2FA with password confirmation."""
    user_type = _user_type(principal)
    await disable_two_factor_with_password(principal.user_id, user_type, data.password)
    return {"disabled": True}


@router.post("/2fa/backup-codes/regenerate")
@document_response(
    message="Backup codes regenerated",
    success_example={"backupCodes": ["ABC12345", "DEF67890", "GHI13579"]},
    description="Regenerate backup codes (invalidates old ones). Requires password for confirmation.",
    summary="Regenerate backup codes",
    response_codes={
        401: "Unauthorized — invalid password",
        404: "2FA is not currently enabled",
    },
)
async def regenerate_backup(
    data: BackupCodesRegenerateRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Regenerate backup codes with password confirmation."""
    user_type = _user_type(principal)
    codes = await regenerate_backup_codes_with_password(
        principal.user_id,
        user_type,
        data.password,
    )
    return {"backup_codes": codes}


# ═══════════════════════════════════════════════════════════════════
# UNIFIED TOKEN REFRESH
# ═══════════════════════════════════════════════════════════════════


@router.post("/refresh")
@document_response(
    message="Tokens refreshed successfully",
    description=(
        "Role-agnostic token refresh. Auto-detects the caller's role from the "
        "(possibly expired) access token and dispatches to the correct refresh "
        "service. Works for application admins, application users, and any tenant "
        "system user role (super_admin, dept_admin, receptionist, auditor, "
        "security_officer, dpo).\n\n"
        "The expired access token must be supplied in the `Authorization: Bearer` "
        "header (or the `access_token` cookie). The refresh token may be supplied "
        "in the JSON body or the `refresh_token` cookie — if both are present, the "
        "body wins.\n\n"
        "On success, new access and refresh tokens are returned in the response "
        "body and also set as httpOnly cookies. The old token pair is invalidated."
    ),
    summary="Refresh tokens (role-agnostic)",
    response_codes={
        401: "Unauthorized — invalid or mismatched tokens",
        404: "Refresh token not found or already used",
        422: "Validation error — missing refresh token",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired refresh token",
            "code": "AUTH_INVALID_TOKEN",
        },
        404: {
            "success": False,
            "message": "Invalid refresh token",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def refresh_tokens(
    request: Request,
    body: UnifiedRefreshRequest,
    principal: AuthPrincipal = Depends(verify_any_refresh_token),
):
    """Unified refresh endpoint — dispatches to the role-specific refresh service."""
    refresh_token = body.refresh_token or request.cookies.get(REFRESH_TOKEN_COOKIE, "")
    if not refresh_token:
        raise auth_invalid_token(details={"reason": "missing refresh_token"})

    if principal.role == "admin":
        result = await refresh_admin_tokens_reduce_number_of_logins(
            admin_refresh_data=AdminRefresh(refresh_token=refresh_token),
            expired_access_token=principal.access_token_id,
        )
        result.password = ""
    elif principal.role == "user":
        result = await refresh_user_tokens_reduce_number_of_logins(
            user_refresh_data=UserRefresh(refresh_token=refresh_token),
            expired_access_token=principal.access_token_id,
        )
    elif principal.role in TENANT_USER_ROLES:
        result = await refresh_system_user_tokens(
            refresh_data=SystemUserRefresh(refresh_token=refresh_token),
            expired_access_token=principal.access_token_id,
        )
    else:
        # verify_any_refresh_token already guarantees this is unreachable, but be explicit.
        raise auth_invalid_token(details={"role": principal.role})

    return build_auth_response(
        request=request,
        payload=result,
        message="Tokens refreshed successfully",
        previous_access_token_id=principal.access_token_id,
    )
