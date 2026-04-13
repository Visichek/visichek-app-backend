from __future__ import annotations

from fastapi import APIRouter, Depends, status

from core.response_envelope import document_response
from schemas.user_settings_schema import UserSettingsUpdate, UserPreferenceUpdate
from schemas.platform_settings_schema import PlatformSettingsUpdate
from schemas.session_schema import (
    ChangePasswordRequest,
    TwoFactorVerifyRequest,
)
from security.auth import verify_admin_token
from security.account_status_check import check_admin_account_status_and_permissions
from security.principal import AuthPrincipal
from services.user_settings_service import (
    retrieve_or_create_settings,
    update_settings,
    retrieve_preferences,
    save_preference,
)
from services.platform_settings_service import (
    retrieve_or_create_platform_settings,
    update_platform_settings_data,
)
from services.session_service import (
    retrieve_sessions,
    revoke_session,
    revoke_all_sessions_except_current,
)
from services.two_factor_service import (
    setup_two_factor,
    verify_two_factor_setup,
    disable_two_factor,
    regenerate_backup_codes,
)
from services.password_change_service import change_admin_password

router = APIRouter(prefix="/admins", tags=["Admin Settings & Account"])


# ═══════════════════════════════════════════════════════════════════
# PERSONAL SETTINGS
# ═══════════════════════════════════════════════════════════════════


@router.get("/settings")
@document_response(
    message="Settings fetched successfully",
    success_example={
        "theme": "light",
        "language": "en",
        "timezone": "Africa/Lagos",
        "date_format": "DD/MM/YYYY",
        "time_format": "12h",
        "email_notifications": True,
        "push_notifications": False,
        "digest_frequency": "realtime",
    },
    description="Return the authenticated admin's personal settings. Creates defaults on first access.",
    summary="Get admin settings",
    response_codes={401: "Unauthorized"},
)
async def get_admin_settings(
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Get the authenticated admin's personal settings."""
    return await retrieve_or_create_settings(principal.user_id, "admin")


@router.patch("/settings")
@document_response(
    message="Settings updated successfully",
    description="Partial update — only send the fields that changed.",
    summary="Update admin settings",
    response_codes={401: "Unauthorized", 422: "Validation error"},
)
async def update_admin_settings(
    data: UserSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Update the authenticated admin's personal settings."""
    return await update_settings(principal.user_id, "admin", data)


# ═══════════════════════════════════════════════════════════════════
# PREFERENCES (key-value store)
# ═══════════════════════════════════════════════════════════════════


@router.get("/preferences")
@document_response(
    message="Preferences fetched successfully",
    success_example={
        "dashboard_action_order": ["create-tenant", "create-plan"],
        "dashboard_collapsed_sections": ["metrics"],
    },
    description="Get all key-value preferences for the authenticated admin.",
    summary="Get admin preferences",
    response_codes={401: "Unauthorized"},
)
async def get_admin_preferences(
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Get all preferences for the authenticated admin."""
    return await retrieve_preferences(principal.user_id, "admin")


@router.patch("/preferences")
@document_response(
    message="Preference saved successfully",
    description="Save a single preference key-value pair. Max 50 keys, 16 KB per value.",
    summary="Save admin preference",
    response_codes={
        401: "Unauthorized",
        422: "Validation error — key limit or size exceeded",
    },
)
async def save_admin_preference(
    data: UserPreferenceUpdate,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Save a single preference key-value pair."""
    return await save_preference(principal.user_id, "admin", data.key, data.value)


# ═══════════════════════════════════════════════════════════════════
# PLATFORM SETTINGS (application admin only)
# ═══════════════════════════════════════════════════════════════════


@router.get("/platform-settings")
@document_response(
    message="Platform settings fetched successfully",
    success_example={
        "platform_name": "VisiChek",
        "support_email": "support@visichek.com",
        "maintenance_mode": False,
        "signups_enabled": True,
        "default_trial_days": 14,
    },
    description="Get global platform configuration. Application admin only.",
    summary="Get platform settings",
    response_codes={401: "Unauthorized", 403: "Forbidden"},
)
async def get_platform_settings(
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Get platform settings (application admin only)."""
    return await retrieve_or_create_platform_settings()


@router.patch("/platform-settings")
@document_response(
    message="Platform settings updated successfully",
    description="Partial update of global platform configuration. Application admin only.",
    summary="Update platform settings",
    response_codes={401: "Unauthorized", 403: "Forbidden", 422: "Validation error"},
)
async def update_platform_settings_endpoint(
    data: PlatformSettingsUpdate,
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Update platform settings (application admin only)."""
    return await update_platform_settings_data(data, actor_id=admin.id)


# ═══════════════════════════════════════════════════════════════════
# PASSWORD CHANGE
# ═══════════════════════════════════════════════════════════════════


@router.post("/change-password")
@document_response(
    message="Password changed successfully",
    success_example={"changed": True},
    description="Change the authenticated admin's password. Validates current password and password policy.",
    summary="Change admin password",
    response_codes={
        401: "Unauthorized — current password is wrong",
        422: "Validation error — new password doesn't meet policy",
    },
)
async def change_password(
    data: ChangePasswordRequest,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Change admin password."""
    await change_admin_password(
        principal.user_id, data.current_password, data.new_password
    )
    return {"changed": True}


# ═══════════════════════════════════════════════════════════════════
# TWO-FACTOR AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════


@router.post("/2fa/setup")
@document_response(
    message="2FA setup initiated",
    status_code=status.HTTP_201_CREATED,
    success_example={
        "secret": "JBSWY3DPEHPK3PXP",
        "qr_code_uri": "otpauth://totp/VisiChek:admin@example.com?...",
        "backup_codes": ["A1B2C3D4", "E5F6G7H8"],
    },
    description="Initiate TOTP 2FA setup. Returns secret, QR code URI, and backup codes. Not active until verified.",
    summary="Setup 2FA",
    response_codes={401: "Unauthorized", 409: "Conflict — 2FA already enabled"},
)
async def setup_2fa(
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Initiate 2FA setup for the authenticated admin."""
    email = getattr(admin, "email", "admin@visichek.com")
    return await setup_two_factor(admin.id, "admin", email)


@router.post("/2fa/verify")
@document_response(
    message="2FA verified and enabled",
    success_example={"enabled": True},
    description="Confirm 2FA setup by providing a valid TOTP code.",
    summary="Verify 2FA setup",
    response_codes={
        401: "Unauthorized — invalid code",
        404: "No pending 2FA setup found",
    },
)
async def verify_2fa(
    data: TwoFactorVerifyRequest,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Verify TOTP code to activate 2FA."""
    await verify_two_factor_setup(principal.user_id, "admin", data.code)
    return {"enabled": True}


@router.post("/2fa/disable")
@document_response(
    message="2FA disabled",
    success_example={"disabled": True},
    description="Disable 2FA after verifying current TOTP code.",
    summary="Disable 2FA",
    response_codes={
        401: "Unauthorized — invalid code",
        404: "2FA is not enabled",
    },
)
async def disable_2fa(
    data: TwoFactorVerifyRequest,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Disable 2FA for the authenticated admin."""
    await disable_two_factor(principal.user_id, "admin", data.code)
    return {"disabled": True}


@router.post("/2fa/backup-codes")
@document_response(
    message="Backup codes regenerated",
    success_example={"backup_codes": ["A1B2C3D4", "E5F6G7H8"]},
    description="Generate new backup codes, invalidating the old ones.",
    summary="Regenerate backup codes",
    response_codes={401: "Unauthorized", 404: "2FA is not enabled"},
)
async def regenerate_backup(
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Regenerate backup codes."""
    codes = await regenerate_backup_codes(principal.user_id, "admin")
    return {"backup_codes": codes}


# ═══════════════════════════════════════════════════════════════════
# SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════


@router.get("/sessions")
@document_response(
    message="Sessions fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "ip_address": "192.168.1.1",
            "device_type": "desktop",
            "last_active_at": 1712500000,
            "is_current": True,
        }
    ],
    description="List all active sessions for the authenticated admin.",
    summary="List admin sessions",
    response_codes={401: "Unauthorized"},
)
async def list_sessions(
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """List active sessions."""
    return await retrieve_sessions(
        principal.user_id,
        "admin",
        current_token_id=principal.access_token_id,
    )


@router.delete("/sessions/{session_id}")
@document_response(
    message="Session revoked",
    success_example={"revoked": True},
    description="Revoke a specific session.",
    summary="Revoke session",
    response_codes={401: "Unauthorized", 404: "Session not found"},
)
async def revoke_single_session(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Revoke a specific session."""
    await revoke_session(session_id, principal.user_id, "admin")
    return {"revoked": True}


@router.delete("/sessions")
@document_response(
    message="All other sessions revoked",
    success_example={"revoked_count": 3},
    description="Revoke all sessions except the current one.",
    summary="Revoke all other sessions",
    response_codes={401: "Unauthorized"},
)
async def revoke_all_other_sessions(
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    """Revoke all sessions except current."""
    count = await revoke_all_sessions_except_current(
        principal.user_id,
        "admin",
        principal.access_token_id,
    )
    return {"revoked_count": count}
