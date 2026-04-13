from __future__ import annotations

from typing import Any, Optional

from core.database import db
from schemas.user_settings_schema import (
    UserSettingsCreate,
    UserSettingsUpdate,
    UserSettingsOut,
)

COLLECTION = "user_settings"
PREFERENCES_COLLECTION = "user_preferences"


# --- User Settings ---


async def create_user_settings(data: UserSettingsCreate) -> UserSettingsOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return UserSettingsOut(**new_doc)


async def get_user_settings(filter_dict: dict) -> Optional[UserSettingsOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return UserSettingsOut(**doc)
    return None


async def update_user_settings(
    filter_dict: dict, data: UserSettingsUpdate
) -> Optional[UserSettingsOut]:
    update_fields = data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_user_settings(filter_dict)

    result = await db[COLLECTION].update_one(filter_dict, {"$set": update_fields})
    if result.matched_count == 0:
        return None
    return await get_user_settings(filter_dict)


# --- User Preferences (key-value store) ---


async def get_user_preferences(user_id: str, user_type: str) -> dict[str, Any]:
    doc = await db[PREFERENCES_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type}
    )
    if doc:
        return doc.get("preferences", {})
    return {}


async def set_user_preference(
    user_id: str, user_type: str, key: str, value: Any
) -> dict[str, Any]:
    """Upsert a single preference key-value pair."""
    await db[PREFERENCES_COLLECTION].update_one(
        {"user_id": user_id, "user_type": user_type},
        {
            "$set": {f"preferences.{key}": value},
            "$setOnInsert": {"user_id": user_id, "user_type": user_type},
        },
        upsert=True,
    )
    return await get_user_preferences(user_id, user_type)


async def count_user_preference_keys(user_id: str, user_type: str) -> int:
    prefs = await get_user_preferences(user_id, user_type)
    return len(prefs)
