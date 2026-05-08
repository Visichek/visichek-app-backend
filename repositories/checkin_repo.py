from __future__ import annotations

from typing import Optional

from bson import ObjectId

from core.database import db
from schemas.checkin_schema import CheckinCreate, CheckinOut, CheckinUpdate

COLLECTION = "checkins"


def _coerce_id_filter(filter_dict: dict) -> dict:
    """Return a copy of ``filter_dict`` with ``_id`` coerced to ObjectId when it
    was passed as a hex string. Check-in _ids are stored as ObjectId in MongoDB
    but ``CheckinOut`` exposes them as strings, so callers passing ``checkin.id``
    back into a query would otherwise match nothing."""
    if "_id" not in filter_dict:
        return filter_dict
    raw = filter_dict["_id"]
    if isinstance(raw, str) and ObjectId.is_valid(raw):
        coerced = dict(filter_dict)
        coerced["_id"] = ObjectId(raw)
        return coerced
    return filter_dict


async def create_checkin(payload: CheckinCreate) -> CheckinOut:
    """Create a new check-in."""
    checkin_dict = payload.model_dump()
    result = await db[COLLECTION].insert_one(checkin_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return CheckinOut(**doc)


async def get_checkin(filter_dict: dict) -> Optional[CheckinOut]:
    """Fetch a single check-in."""
    doc = await db[COLLECTION].find_one(_coerce_id_filter(filter_dict))
    if doc is None:
        return None
    return CheckinOut(**doc)


async def get_checkins(
    filter_dict: dict = {},
    skip: int = 0,
    limit: int = 20,
    sort: Optional[list] = None,
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
        _coerce_id_filter({"_id": checkin_id}),
        {"$set": update_dict},
        return_document=True,
    )
    if result is None:
        raise ValueError(f"Checkin {checkin_id} not found")
    return CheckinOut(**result)


async def count_checkins(filter_dict: dict) -> int:
    """Count check-ins matching a filter."""
    return await db[COLLECTION].count_documents(filter_dict)


def _approved_for_checkout_filter(tenant_id: str) -> dict:
    return {"tenant_id": tenant_id, "state": "approved"}


async def get_approved_checkins_for_checkout(
    tenant_id: str, start: int = 0, stop: int = 50
) -> list[CheckinOut]:
    """Approved check-ins that have not been checked out yet."""
    return await get_checkins(
        _approved_for_checkout_filter(tenant_id),
        skip=start,
        limit=stop - start,
        sort=[("approved_at", -1), ("date_created", -1)],
    )


async def count_approved_checkins_for_checkout(tenant_id: str) -> int:
    """Count approved check-ins that are still awaiting checkout."""
    return await count_checkins(_approved_for_checkout_filter(tenant_id))


async def get_active_pending_for_visitor(
    tenant_id: str, visitor_id: str
) -> Optional[CheckinOut]:
    """Get any in-flight check-in for a visitor in a tenant.

    "In-flight" covers both ``pending_kyc`` (KYC widget running) and
    ``pending_approval`` (receptionist hasn't acted yet) so a visitor
    can't accidentally spawn a duplicate by re-submitting while their
    first check-in is still working through the state machine.
    """
    doc = await db[COLLECTION].find_one(
        {
            "tenant_id": tenant_id,
            "visitor_id": visitor_id,
            "state": {"$in": ["pending_kyc", "pending_approval"]},
        }
    )
    if doc is None:
        return None
    return CheckinOut(**doc)
