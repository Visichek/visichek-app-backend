from __future__ import annotations

import json
from typing import Any, cast

from fastapi import HTTPException

from core.redis_cache import cache_db
from schemas.imports import UserType
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

# Short-TTL cache for the maintenance flag so the hot request path
# (PlanEnforcementMiddleware) doesn't read the singleton from Mongo on every
# tenant request. Invalidated explicitly whenever the flag is toggled.
_MAINTENANCE_CACHE_KEY = "platform:maintenance_state"
_MAINTENANCE_CACHE_TTL = 30  # seconds


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


async def get_maintenance_state() -> dict:
    """Return ``{"mode": bool, "message": Optional[str]}`` for the platform.

    Reads from a short-TTL Redis cache, falling back to a direct DB read on a
    cache miss. Designed for the hot request path. Fails OPEN — any cache/DB
    error resolves to "not in maintenance" so a transient infra blip can never
    lock every tenant out of the platform.
    """
    try:
        raw = cast(Any, cache_db.get(_MAINTENANCE_CACHE_KEY))
        if raw is not None:
            return json.loads(raw)
    except Exception:
        pass  # Cache miss/failure → fall through to DB

    state: dict[str, Any] = {"mode": False, "message": None}
    try:
        settings = await get_platform_settings()
        if settings:
            state = {
                "mode": bool(settings.maintenance_mode),
                "message": settings.maintenance_message,
            }
    except Exception:
        # Never block the platform because of a DB blip.
        return {"mode": False, "message": None}

    try:
        cache_db.setex(
            _MAINTENANCE_CACHE_KEY, _MAINTENANCE_CACHE_TTL, json.dumps(state)
        )
    except Exception:
        pass  # Non-critical: re-read from DB next time
    return state


def invalidate_maintenance_cache() -> None:
    """Drop the cached maintenance flag so the next read sees fresh state."""
    try:
        cache_db.delete(_MAINTENANCE_CACHE_KEY)
    except Exception:
        pass


async def request_maintenance_mode_otp(actor_id: str) -> str:
    """Step 1: mint an OTP challenge for a maintenance-mode change.

    Returns the challenge id. The code is delivered out-of-band (email for
    invited admins, static dev code for the env primary admin / non-prod).
    """
    from services.otp_service import create_otp_challenge

    challenge_id, _code = await create_otp_challenge(
        user_id=actor_id,
        user_type=UserType.ADMIN,
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

    # Drop the cached flag so the middleware picks up the new state at once.
    invalidate_maintenance_cache()

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
