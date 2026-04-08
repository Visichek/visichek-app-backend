from __future__ import annotations

from fastapi import APIRouter, Depends, status

from core.response_envelope import document_response
from schemas.user_settings_schema import UserSettingsOut, UserSettingsUpdate, UserPreferenceUpdate
from schemas.session_schema import (
    ChangePasswordRequest,
    SessionOut,
    TwoFactorSetupOut,
    TwoFactorVerifyRequest,
)
from security.auth import verify_any_system_user_token
from security.principal import AuthPrincipal
from services.user_settings_service import (
    retrieve_or_create_settings,
    update_settings,
    retrieve_preferences,
    save_preference,
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
from services.password_change_service import change_system_user_password

router = APIRouter(prefix="/system-users", tags=["System User Settings & Account"])


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
    description="Return the authenticated system user's personal settings. Creates defaults on first access.",
    summary="Get system user settings",
    response_codes={401: "Unauthorized"},
)
async def get_system_user_settings(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Get the authenticated system user's personal settings."""
    return await retrieve_or_create_settings(principal.user_id, "system_user")


@router.patch("/settings")
@document_response(
    message="Settings updated successfully",
    description="Partial update — only send the fields that changed.",
    summary="Update system user settings",
    response_codes={401: "Unauthorized", 422: "Validation error"},
)
async def update_system_user_settings(
    data: UserSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Update the authenticated system user's personal settings."""
    return await update_settings(principal.user_id, "system_user", data)


# ═══════════════════════════════════════════════════════════════════
# PREFERENCES (key-value store)
# ═══════════════════════════════════════════════════════════════════


@router.get("/preferences")
@document_response(
    message="Preferences fetched successfully",
    success_example={
        "dashboard_action_order": ["visitors", "departments"],
        "dashboard_collapsed_sections": [],
    },
    description="Get all key-value preferences for the authenticated system user.",
    summary="Get system user preferences",
    response_codes={401: "Unauthorized"},
)
async def get_system_user_preferences(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Get all preferences for the authenticated system user."""
    return await retrieve_preferences(principal.user_id, "system_user")


@router.patch("/preferences")
@document_response(
    message="Preference saved successfully",
    description="Save a single preference key-value pair. Max 50 keys, 16 KB per value.",
    summary="Save system user preference",
    response_codes={401: "Unauthorized", 422: "Validation error — key limit or size exceeded"},
)
async def save_system_user_preference(
    data: UserPreferenceUpdate,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Save a single preference key-value pair."""
    return await save_preference(principal.user_id, "system_user", data.key, data.value)


# ═══════════════════════════════════════════════════════════════════
# PASSWORD CHANGE
# ═══════════════════════════════════════════════════════════════════


@router.post("/change-password")
@document_response(
    message="Password changed successfully",
    success_example={"changed": True},
    description="Change the authenticated system user's password. Validates current password and password policy.",
    summary="Change system user password",
    response_codes={
        401: "Unauthorized — current password is wrong",
        422: "Validation error — new password doesn't meet policy",
    },
)
async def change_password(
    data: ChangePasswordRequest,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Change system user password."""
    await change_system_user_password(principal.user_id, data.current_password, data.new_password)
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
        "qr_code_uri": "otpauth://totp/VisiChek:user@example.com?...",
        "backup_codes": ["A1B2C3D4", "E5F6G7H8"],
    },
    description="Initiate TOTP 2FA setup. Returns secret, QR code URI, and backup codes. Not active until verified.",
    summary="Setup 2FA",
    response_codes={401: "Unauthorized", 409: "Conflict — 2FA already enabled"},
)
async def setup_2fa(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Initiate 2FA setup for the authenticated system user."""
    from repositories.system_user_repo import get_system_user
    from bson import ObjectId
    user = await get_system_user({"_id": ObjectId(principal.user_id)})
    email = user.email if user else "user@visichek.com"
    return await setup_two_factor(principal.user_id, "system_user", email)


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
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Verify TOTP code to activate 2FA."""
    await verify_two_factor_setup(principal.user_id, "system_user", data.code)
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
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Disable 2FA for the authenticated system user."""
    await disable_two_factor(principal.user_id, "system_user", data.code)
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
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Regenerate backup codes."""
    codes = await regenerate_backup_codes(principal.user_id, "system_user")
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
    description="List all active sessions for the authenticated system user.",
    summary="List system user sessions",
    response_codes={401: "Unauthorized"},
)
async def list_sessions(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """List active sessions."""
    return await retrieve_sessions(
        principal.user_id,
        "system_user",
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
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Revoke a specific session."""
    await revoke_session(session_id, principal.user_id, "system_user")
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
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Revoke all sessions except current."""
    count = await revoke_all_sessions_except_current(
        principal.user_id, "system_user", principal.access_token_id,
    )
    return {"revoked_count": count}
