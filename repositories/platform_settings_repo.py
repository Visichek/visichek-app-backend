from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.platform_settings_schema import (
    PlatformSettingsCreate,
    PlatformSettingsUpdate,
    PlatformSettingsOut,
)

COLLECTION = "platform_settings"


async def create_platform_settings(data: PlatformSettingsCreate) -> PlatformSettingsOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return PlatformSettingsOut(**new_doc)


async def get_platform_settings() -> Optional[PlatformSettingsOut]:
    """Get the singleton platform settings document."""
    doc = await db[COLLECTION].find_one({})
    if doc:
        return PlatformSettingsOut(**doc)
    return None


async def update_platform_settings(
    data: PlatformSettingsUpdate,
) -> Optional[PlatformSettingsOut]:
    """Update the singleton platform settings document."""
    update_fields = data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_platform_settings()

    result = await db[COLLECTION].update_one({}, {"$set": update_fields})
    if result.matched_count == 0:
        return None
    return await get_platform_settings()
