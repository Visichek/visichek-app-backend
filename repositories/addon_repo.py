"""Addon catalog repository (admin-managed catalog rows)."""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from core.database import db
from schemas.addon_schema import AddonCreate, AddonOut, AddonUpdate

COLLECTION = "addons"


async def create_addon(
    payload: AddonCreate, *, preassigned_id: Optional[str] = None
) -> AddonOut:
    doc = payload.model_dump()
    if preassigned_id and ObjectId.is_valid(preassigned_id):
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    saved = await db[COLLECTION].find_one({"_id": result.inserted_id})
    assert saved is not None
    return AddonOut(**saved)


async def get_addon(filter_dict: dict) -> Optional[AddonOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    return AddonOut(**doc) if doc else None


async def get_addon_by_id(addon_id: str) -> Optional[AddonOut]:
    if not ObjectId.is_valid(addon_id):
        return None
    return await get_addon({"_id": ObjectId(addon_id)})


async def list_addons(
    filter_dict: dict, skip: int = 0, limit: int = 100
) -> List[AddonOut]:
    cursor = db[COLLECTION].find(filter_dict).skip(skip).limit(limit)
    return [AddonOut(**doc) async for doc in cursor]


async def update_addon(addon_id: str, payload: AddonUpdate) -> Optional[AddonOut]:
    if not ObjectId.is_valid(addon_id):
        return None
    update_fields = payload.model_dump(exclude_unset=True)
    if not update_fields:
        return await get_addon_by_id(addon_id)
    await db[COLLECTION].update_one(
        {"_id": ObjectId(addon_id)}, {"$set": update_fields}
    )
    return await get_addon_by_id(addon_id)


async def delete_addon(addon_id: str) -> int:
    if not ObjectId.is_valid(addon_id):
        return 0
    result = await db[COLLECTION].delete_one({"_id": ObjectId(addon_id)})
    return getattr(result, "deleted_count", 0) or 0


async def count_addons(filter_dict: dict) -> int:
    return await db[COLLECTION].count_documents(filter_dict)
