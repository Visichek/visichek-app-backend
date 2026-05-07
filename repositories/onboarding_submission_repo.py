from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.onboarding_submission_schema import (
    OnboardingSubmissionCreate,
    OnboardingSubmissionOut,
    OnboardingSubmissionUpdate,
)

COLLECTION = "onboarding_submissions"


async def create_submission(
    data: OnboardingSubmissionCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> OnboardingSubmissionOut:
    doc = data.model_dump()
    if preassigned_id:
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return OnboardingSubmissionOut(**new_doc)  # type: ignore[arg-type]


async def get_submission(filter_dict: dict) -> Optional[OnboardingSubmissionOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return OnboardingSubmissionOut(**doc)


async def get_submission_by_id(submission_id: str) -> Optional[OnboardingSubmissionOut]:
    if not ObjectId.is_valid(submission_id):
        return None
    return await get_submission({"_id": ObjectId(submission_id)})


async def list_submissions(
    filter_dict: Optional[dict] = None,
    *,
    skip: int = 0,
    limit: int = 50,
) -> List[OnboardingSubmissionOut]:
    cursor = (
        db[COLLECTION]
        .find(filter_dict or {})
        .sort("submitted_at", -1)
        .skip(skip)
        .limit(limit)
    )
    out: List[OnboardingSubmissionOut] = []
    async for doc in cursor:
        out.append(OnboardingSubmissionOut(**doc))
    return out


async def count_submissions(filter_dict: Optional[dict] = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})


async def update_submission(
    filter_dict: dict, data: OnboardingSubmissionUpdate
) -> Optional[OnboardingSubmissionOut]:
    update_fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not update_fields:
        return await get_submission(filter_dict)
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return OnboardingSubmissionOut(**result)


async def update_submission_by_id(
    submission_id: str, data: OnboardingSubmissionUpdate
) -> Optional[OnboardingSubmissionOut]:
    if not ObjectId.is_valid(submission_id):
        return None
    return await update_submission({"_id": ObjectId(submission_id)}, data)
