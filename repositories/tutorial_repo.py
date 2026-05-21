# ============================================================================
# TUTORIAL REPOSITORY
# ============================================================================
# Pure database access for per-user tutorial progress. One document per
# (user_id, tutorial_type, version) — see core/indexes.py for the
# partial-unique index that enforces it. No business logic here.
# ============================================================================

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.tutorial_schema import TutorialUpdate, TutorialCreate, TutorialOut

COLLECTION = "tutorials"


async def create_tutorial(
    tutorial_data: TutorialCreate, preassigned_id: Optional[str] = None
) -> TutorialOut:
    tutorial_dict = tutorial_data.model_dump()
    if preassigned_id is not None:
        tutorial_dict["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(tutorial_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return TutorialOut(**doc)  # type: ignore


async def get_tutorial(filter_dict: dict) -> Optional[TutorialOut]:
    result = await db[COLLECTION].find_one(filter_dict)
    if result is None:
        return None
    return TutorialOut(**result)


async def get_tutorials(
    filter_dict: Optional[dict] = None, skip: int = 0, limit: int = 100
) -> List[TutorialOut]:
    cursor = db[COLLECTION].find(filter_dict or {}).skip(skip).limit(limit)
    return [TutorialOut(**doc) async for doc in cursor]


async def update_tutorial(
    filter_dict: dict, tutorial_data: TutorialUpdate
) -> Optional[TutorialOut]:
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": tutorial_data.model_dump(exclude_none=True)},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return TutorialOut(**result)


async def delete_tutorial(filter_dict: dict) -> int:
    result = await db[COLLECTION].delete_one(filter_dict)
    return result.deleted_count
