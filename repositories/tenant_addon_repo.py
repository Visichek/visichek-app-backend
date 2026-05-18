"""Tenant addon instances repository.

Each row links a tenant to one purchase of an addon. Status drives
whether the benefit is counted toward quotas — ``active`` rows whose
``expires_at`` is in the future are summed; everything else is
ignored.
"""

from __future__ import annotations

import time
from typing import List, Optional

from bson import ObjectId

from core.database import db
from schemas.addon_schema import (
    TenantAddonCreate,
    TenantAddonOut,
    TenantAddonStatus,
    TenantAddonUpdate,
)

COLLECTION = "tenant_addons"


async def create_tenant_addon(
    payload: TenantAddonCreate, *, preassigned_id: Optional[str] = None
) -> TenantAddonOut:
    doc = payload.model_dump()
    if preassigned_id and ObjectId.is_valid(preassigned_id):
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    saved = await db[COLLECTION].find_one({"_id": result.inserted_id})
    assert saved is not None
    return TenantAddonOut(**saved)


async def get_tenant_addon(filter_dict: dict) -> Optional[TenantAddonOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    return TenantAddonOut(**doc) if doc else None


async def get_tenant_addon_by_id(addon_id: str) -> Optional[TenantAddonOut]:
    if not ObjectId.is_valid(addon_id):
        return None
    return await get_tenant_addon({"_id": ObjectId(addon_id)})


async def get_tenant_addon_by_reference(
    payment_reference: str,
) -> Optional[TenantAddonOut]:
    if not payment_reference:
        return None
    return await get_tenant_addon({"payment_reference": payment_reference})


async def list_tenant_addons(
    filter_dict: dict, skip: int = 0, limit: int = 200
) -> List[TenantAddonOut]:
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("date_created", -1)
        .skip(skip)
        .limit(limit)
    )
    return [TenantAddonOut(**doc) async for doc in cursor]


async def list_active_for_tenant(
    tenant_id: str, *, addon_kind: Optional[str] = None
) -> List[TenantAddonOut]:
    """Return only active (paid, not yet expired) addon instances.

    Used by the storage / quota service to sum up usable benefit.
    Rows with a NULL ``expires_at`` are treated as perpetual.
    """
    now = int(time.time())
    filt: dict = {
        "tenant_id": tenant_id,
        "status": TenantAddonStatus.ACTIVE.value,
        "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
    }
    if addon_kind:
        filt["addon_kind"] = addon_kind
    cursor = db[COLLECTION].find(filt)
    return [TenantAddonOut(**doc) async for doc in cursor]


async def update_tenant_addon(
    addon_id: str, payload: TenantAddonUpdate
) -> Optional[TenantAddonOut]:
    if not ObjectId.is_valid(addon_id):
        return None
    update_fields = payload.model_dump(exclude_unset=True)
    if not update_fields:
        return await get_tenant_addon_by_id(addon_id)
    await db[COLLECTION].update_one(
        {"_id": ObjectId(addon_id)}, {"$set": update_fields}
    )
    return await get_tenant_addon_by_id(addon_id)


async def expire_due_tenant_addons() -> int:
    """Mark all active addons whose ``expires_at`` is in the past as expired.

    Called by the scheduled job ``addon.expire_due`` so a forgotten
    addon stops being counted toward quotas without per-request work.
    Returns the count of rows transitioned.
    """
    now = int(time.time())
    result = await db[COLLECTION].update_many(
        {
            "status": TenantAddonStatus.ACTIVE.value,
            "expires_at": {"$ne": None, "$lte": now},
        },
        {
            "$set": {
                "status": TenantAddonStatus.EXPIRED.value,
                "last_updated": now,
            }
        },
    )
    return getattr(result, "modified_count", 0) or 0


async def count_tenant_addons(filter_dict: dict) -> int:
    return await db[COLLECTION].count_documents(filter_dict)
