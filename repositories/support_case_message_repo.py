from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from core.database import db
from schemas.support_case_schema import (
    SupportCaseMessageCreate,
    SupportCaseMessageOut,
)

COLLECTION = "support_case_messages"


async def create_message(
    message: SupportCaseMessageCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> SupportCaseMessageOut:
    doc = message.model_dump()
    if preassigned_id and ObjectId.is_valid(preassigned_id):
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    stored = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return SupportCaseMessageOut(**stored)  # type: ignore[arg-type]


async def list_messages_for_case(
    case_id: str,
    include_internal: bool,
    start: int = 0,
    stop: int = 200,
) -> List[SupportCaseMessageOut]:
    filter_dict: dict = {"case_id": case_id}
    if not include_internal:
        filter_dict["internal_note"] = False
    limit = max(stop - start, 0)
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("date_created", 1)
        .skip(start)
        .limit(limit)
    )
    return [SupportCaseMessageOut(**doc) async for doc in cursor]


async def get_message_by_id(message_id: str) -> Optional[SupportCaseMessageOut]:
    if not ObjectId.is_valid(message_id):
        return None
    doc = await db[COLLECTION].find_one({"_id": ObjectId(message_id)})
    if doc is None:
        return None
    return SupportCaseMessageOut(**doc)


async def count_messages_for_case(case_id: str, include_internal: bool = True) -> int:
    filter_dict: dict = {"case_id": case_id}
    if not include_internal:
        filter_dict["internal_note"] = False
    return await db[COLLECTION].count_documents(filter_dict)
