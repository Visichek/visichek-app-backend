from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.trial_code_schema import (
    TrialCodeCreate,
    TrialCodeOut,
    TrialCodeUpdate,
)

COLLECTION = "trial_codes"


async def create_trial_code(
    data: TrialCodeCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> TrialCodeOut:
    doc = data.model_dump(mode="json")
    if preassigned_id:
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    fresh = await db[COLLECTION].find_one({"_id": result.inserted_id})
    assert fresh is not None
    return TrialCodeOut(**fresh)


async def get_trial_code(filter_dict: dict) -> Optional[TrialCodeOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return TrialCodeOut(**doc)


async def get_trial_codes(
    filter_dict: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
) -> List[TrialCodeOut]:
    cursor = db[COLLECTION].find(filter_dict or {}).skip(start).limit(stop - start)
    return [TrialCodeOut(**doc) async for doc in cursor]


async def update_trial_code(
    filter_dict: dict, data: TrialCodeUpdate
) -> Optional[TrialCodeOut]:
    update_dict = {
        k: v for k, v in data.model_dump(mode="json").items() if v is not None
    }
    if not update_dict:
        return await get_trial_code(filter_dict)
    doc = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    return TrialCodeOut(**doc)


async def get_tenant_redeemed_trial(tenant_id: str) -> Optional[TrialCodeOut]:
    """Has the tenant ever successfully used a trial? Returns the row if so."""
    return await get_trial_code(
        {"tenant_id": tenant_id, "status": "used"},
    )


async def get_tenant_pending_trial(
    tenant_id: str, plan_id: Optional[str] = None
) -> Optional[TrialCodeOut]:
    """Find a still-claimable trial code for a tenant.

    When ``plan_id`` is provided we scope to that plan (so the tenant can
    receive the same code if they re-claim the same plan); otherwise the
    most recent pending row is returned (used by status lookups).
    """
    filter_dict: dict = {"tenant_id": tenant_id, "status": "pending"}
    if plan_id is not None:
        filter_dict["plan_id"] = plan_id
    return await get_trial_code(filter_dict)
