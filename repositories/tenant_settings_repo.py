from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.tenant_settings_schema import (
    TenantSettingsCreate,
    TenantSettingsUpdate,
    TenantSettingsOut,
)

COLLECTION = "tenant_settings"


async def create_tenant_settings(data: TenantSettingsCreate) -> TenantSettingsOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return TenantSettingsOut(**new_doc)


async def get_tenant_settings(filter_dict: dict) -> Optional[TenantSettingsOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return TenantSettingsOut(**doc)
    return None


async def update_tenant_settings(
    filter_dict: dict,
    data: TenantSettingsUpdate,
) -> Optional[TenantSettingsOut]:
    update_fields = data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_tenant_settings(filter_dict)

    result = await db[COLLECTION].update_one(filter_dict, {"$set": update_fields})
    if result.matched_count == 0:
        return None
    return await get_tenant_settings(filter_dict)


async def delete_tenant_settings(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)
