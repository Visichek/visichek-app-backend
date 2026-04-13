from __future__ import annotations

from typing import List, Optional

from core.database import db
from schemas.notification_schema import (
    NotificationCreate,
    NotificationUpdate,
    NotificationOut,
    NotificationPreferencesCreate,
    NotificationPreferencesUpdate,
    NotificationPreferencesOut,
)

COLLECTION = "notifications"
PREFS_COLLECTION = "notification_preferences"


# --- Notifications ---


async def create_notification(data: NotificationCreate) -> NotificationOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return NotificationOut(**new_doc)  # type: ignore


async def get_notification(filter_dict: dict) -> Optional[NotificationOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return NotificationOut(**doc)
    return None


async def get_notifications(
    filter_dict: dict,
    skip: int = 0,
    limit: int = 20,
) -> List[NotificationOut]:
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("date_created", -1)
        .skip(skip)
        .limit(limit)
    )
    items = []
    async for doc in cursor:
        items.append(NotificationOut(**doc))
    return items


async def count_notifications(filter_dict: dict) -> int:
    return await db[COLLECTION].count_documents(filter_dict)


async def update_notification(
    filter_dict: dict, data: NotificationUpdate
) -> Optional[NotificationOut]:
    update_fields = data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_notification(filter_dict)

    await db[COLLECTION].update_one(filter_dict, {"$set": update_fields})
    return await get_notification(filter_dict)


async def mark_all_read(user_id: str, user_type: str) -> int:
    result = await db[COLLECTION].update_many(
        {"user_id": user_id, "user_type": user_type, "read": False},
        {"$set": {"read": True}},
    )
    return result.modified_count


async def delete_notification(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)


# --- Notification Preferences ---


async def create_notification_preferences(
    data: NotificationPreferencesCreate,
) -> NotificationPreferencesOut:
    doc = data.model_dump()
    result = await db[PREFS_COLLECTION].insert_one(doc)
    new_doc = await db[PREFS_COLLECTION].find_one({"_id": result.inserted_id})
    return NotificationPreferencesOut(**new_doc)  # type: ignore


async def get_notification_preferences(
    filter_dict: dict,
) -> Optional[NotificationPreferencesOut]:
    doc = await db[PREFS_COLLECTION].find_one(filter_dict)
    if doc:
        return NotificationPreferencesOut(**doc)
    return None


async def update_notification_preferences(
    filter_dict: dict,
    data: NotificationPreferencesUpdate,
) -> Optional[NotificationPreferencesOut]:
    update_fields = data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_notification_preferences(filter_dict)

    await db[PREFS_COLLECTION].update_one(filter_dict, {"$set": update_fields})
    return await get_notification_preferences(filter_dict)
