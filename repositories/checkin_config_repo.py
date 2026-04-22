from __future__ import annotations

from typing import Optional

from bson import ObjectId

from core.database import db
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigOut,
    CheckinConfigUpdate,
)

COLLECTION = "checkin_configs"


async def create_checkin_config(
    payload: CheckinConfigCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> CheckinConfigOut:
    """Create a new check-in configuration."""
    config_dict = payload.model_dump()
    if preassigned_id:
        config_dict["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(config_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return CheckinConfigOut(**doc)


async def get_checkin_config(filter_dict: dict) -> Optional[CheckinConfigOut]:
    """Fetch a single check-in configuration."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return CheckinConfigOut(**doc)


async def get_active_checkin_config_for_tenant(
    tenant_id: str,
) -> Optional[CheckinConfigOut]:
    """Return the most recently updated active config for a tenant, or None."""
    cursor = (
        db[COLLECTION]
        .find({"tenant_id": tenant_id, "active": True})
        .sort("last_updated", -1)
        .limit(1)
    )
    async for doc in cursor:
        return CheckinConfigOut(**doc)
    return None


async def get_checkin_configs(
    filter_dict: dict = {}, skip: int = 0, limit: int = 20
) -> list[CheckinConfigOut]:
    """Fetch multiple check-in configurations with pagination."""
    cursor = db[COLLECTION].find(filter_dict).skip(skip).limit(limit)
    configs = []
    async for doc in cursor:
        configs.append(CheckinConfigOut(**doc))
    return configs


async def update_checkin_config(
    config_id: str, data: CheckinConfigUpdate
) -> CheckinConfigOut:
    """Update a check-in configuration."""
    update_dict = data.model_dump(exclude_unset=True)
    result = await db[COLLECTION].find_one_and_update(
        {"_id": config_id},
        {"$set": update_dict},
        return_document=True,
    )
    if result is None:
        raise ValueError(f"Config {config_id} not found")
    return CheckinConfigOut(**result)


async def delete_checkin_config(config_id: str) -> None:
    """Delete a check-in configuration."""
    await db[COLLECTION].delete_one({"_id": config_id})
