from __future__ import annotations

from typing import Any, Dict, List

from bson import ObjectId

from core.database import db
from schemas.consent_record_schema import ConsentRecordCreate, ConsentRecordOut

COLLECTION = "consent_records"


async def create_consent_record(data: ConsentRecordCreate) -> ConsentRecordOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    saved = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return ConsentRecordOut(**saved)


async def get_consent_records(
    filter_dict: Dict[str, Any] | None = None,
    *,
    skip: int = 0,
    limit: int = 100,
) -> List[dict]:
    filter_dict = filter_dict or {}
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("consent_timestamp", -1)
        .skip(skip)
        .limit(limit)
    )
    results: List[dict] = []
    async for doc in cursor:
        if "_id" in doc and isinstance(doc["_id"], ObjectId):
            doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


async def count_consent_records(filter_dict: Dict[str, Any] | None = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})
