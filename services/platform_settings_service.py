from __future__ import annotations

from fastapi import HTTPException

from repositories.platform_settings_repo import (
    create_platform_settings,
    get_platform_settings,
    update_platform_settings,
)
from schemas.platform_settings_schema import (
    PlatformSettingsCreate,
    PlatformSettingsUpdate,
    PlatformSettingsOut,
    MaintenanceModeUpdateRequest,
)
from services.audit_service import record_audit_event


async def retrieve_or_create_platform_settings() -> PlatformSettingsOut:
    """Get the singleton platform settings, auto-creating defaults on first access.

    Only the runtime-editable subset (maintenance mode) lives in the DB now;
    every other platform knob is sourced from ``config/platform_config.py``.
    """
    existing = await get_platform_settings()
    if existing:
        return existing

    # First access — create default settings
    defaults = PlatformSettingsCreate()
    return await create_platform_settings(defaults)


async def request_maintenance_mode_otp(actor_id: str) -> str:
    """Step 1: mint an OTP challenge for a maintenance-mode change.

    Returns the challenge id. The code is delivered out-of-band (email for
    invited admins, static dev code for the env primary admin / non-prod).
    """
    from services.otp_service import create_otp_challenge

    challenge_id, _code = await create_otp_challenge(
        user_id=actor_id,
        user_type="admin",
        role="admin",
    )
    return challenge_id


async def update_maintenance_mode(
    data: MaintenanceModeUpdateRequest,
    actor_id: str,
) -> PlatformSettingsOut:
    """Step 2: verify the OTP, then flip maintenance mode.

    Raises HTTPException (via ``verify_otp_challenge``) if the OTP is
    invalid, expired, already used, or out of attempts.
    """
    from services.otp_service import verify_otp_challenge

    # Verify before touching state. This enforces attempt counters, expiry,
    # and one-time consumption uniformly with the rest of the OTP system.
    await verify_otp_challenge(data.otp_challenge_id, data.otp_code)

    # Ensure the singleton exists, then apply the maintenance change.
    await retrieve_or_create_platform_settings()

    result = await update_platform_settings(
        PlatformSettingsUpdate(
            maintenance_mode=data.maintenance_mode,
            maintenance_message=data.maintenance_message,
        )
    )
    if not result:
        raise HTTPException(
            status_code=500, detail="Failed to update platform settings"
        )

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action="platform_settings.maintenance_mode_changed",
            resource_type="platform_settings",
            resource_id="singleton",
            details={
                "maintenance_mode": data.maintenance_mode,
                "maintenance_message": data.maintenance_message,
            },
        )
    except Exception:
        pass

    return result
