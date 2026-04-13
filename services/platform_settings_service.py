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
)
from services.audit_service import record_audit_event


async def retrieve_or_create_platform_settings() -> PlatformSettingsOut:
    """Get the singleton platform settings, auto-creating defaults on first access."""
    existing = await get_platform_settings()
    if existing:
        return existing

    # First access — create default settings
    defaults = PlatformSettingsCreate()
    return await create_platform_settings(defaults)


async def update_platform_settings_data(
    data: PlatformSettingsUpdate,
    actor_id: str,
) -> PlatformSettingsOut:
    """Update platform settings, creating defaults first if needed."""
    # Ensure settings record exists
    await retrieve_or_create_platform_settings()

    result = await update_platform_settings(data)
    if not result:
        raise HTTPException(
            status_code=500, detail="Failed to update platform settings"
        )

    # Record audit event (fire-and-forget)
    try:
        changed_fields = data.model_dump(exclude_none=True)
        changed_fields.pop("last_updated", None)
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action="platform_settings.updated",
            resource_type="platform_settings",
            resource_id="singleton",
            details={"changed_fields": list(changed_fields.keys())},
        )
    except Exception:
        pass

    return result
