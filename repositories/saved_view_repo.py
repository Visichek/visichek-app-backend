"""Saved-view (per-user filter sets + column prefs) repository."""

from __future__ import annotations

import time
from typing import Optional

from core.database import db
from schemas.imports import UserType
from schemas.saved_view_schema import SavedViewRecord

COLLECTION = "saved_views"


async def get_saved_view(
    *, user_id: str, user_type: UserType, resource: str
) -> Optional[SavedViewRecord]:
    doc = await db[COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "resource": resource}
    )
    if not doc:
        return None
    return SavedViewRecord(**doc)


async def upsert_saved_view(record: SavedViewRecord) -> SavedViewRecord:
    payload = record.model_dump(by_alias=True, exclude={"id"})
    payload["last_updated"] = int(time.time())
    await db[COLLECTION].update_one(
        {
            "user_id": record.user_id,
            "user_type": record.user_type,
            "resource": record.resource,
        },
        {"$set": payload, "$setOnInsert": {"date_created": int(time.time())}},
        upsert=True,
    )
    doc = await db[COLLECTION].find_one(
        {
            "user_id": record.user_id,
            "user_type": record.user_type,
            "resource": record.resource,
        }
    )
    return SavedViewRecord(**doc) if doc else record


async def list_saved_views_for_user(
    *, user_id: str, user_type: UserType
) -> list[SavedViewRecord]:
    cursor = db[COLLECTION].find({"user_id": user_id, "user_type": user_type})
    return [SavedViewRecord(**doc) async for doc in cursor]


async def delete_saved_view(*, user_id: str, user_type: UserType, resource: str) -> int:
    result = await db[COLLECTION].delete_one(
        {"user_id": user_id, "user_type": user_type, "resource": resource}
    )
    return int(result.deleted_count)
