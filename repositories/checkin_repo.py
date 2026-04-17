from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.checkin_schema import CheckinCreate, CheckinOut, CheckinUpdate

COLLECTION = "checkins"


async def create_checkin(payload: CheckinCreate) -> CheckinOut:
    """Create a new check-in."""
    checkin_dict = payload.model_dump()
    result = await db[COLLECTION].insert_one(checkin_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return CheckinOut(**doc)


async def get_checkin(filter_dict: dict) -> Optional[CheckinOut]:
    """Fetch a single check-in."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return CheckinOut(**doc)


async def get_checkins(
    filter_dict: dict = {},
    skip: int = 0,
    limit: int = 20,
    sort: list = None,
) -> list[CheckinOut]:
    """Fetch multiple check-ins with pagination."""
    if sort is None:
        sort = [("date_created", -1)]
    cursor = db[COLLECTION].find(filter_dict).skip(skip).limit(limit).sort(sort)
    checkins = []
    async for doc in cursor:
        checkins.append(CheckinOut(**doc))
    return checkins


async def update_checkin(checkin_id: str, data: CheckinUpdate) -> CheckinOut:
    """Update a check-in."""
    update_dict = data.model_dump(exclude_unset=True)
    result = await db[COLLECTION].find_one_and_update(
        {"_id": checkin_id},
        {"$set": update_dict},
        return_document=True,
    )
    if result is None:
        raise ValueError(f"Checkin {checkin_id} not found")
    return CheckinOut(**result)


async def count_checkins(filter_dict: dict) -> int:
    """Count check-ins matching a filter."""
    return await db[COLLECTION].count_documents(filter_dict)


async def get_active_pending_for_visitor(
    tenant_id: str, visitor_id: str
) -> Optional[CheckinOut]:
    """Get active pending check-in for a visitor in a tenant."""
    doc = await db[COLLECTION].find_one(
        {
            "tenant_id": tenant_id,
            "visitor_id": visitor_id,
            "state": "pending_approval",
        }
    )
    if doc is None:
        return None
    return CheckinOut(**doc)
