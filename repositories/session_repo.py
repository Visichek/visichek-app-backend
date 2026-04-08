from __future__ import annotations

from bson import ObjectId
from typing import List, Optional

from core.database import db
from schemas.session_schema import SessionCreate, SessionOut

COLLECTION = "sessions"


async def create_session(data: SessionCreate) -> SessionOut:
    doc = data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return SessionOut(**new_doc)


async def get_session(filter_dict: dict) -> Optional[SessionOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return SessionOut(**doc)
    return None


async def get_sessions(filter_dict: dict) -> List[SessionOut]:
    cursor = db[COLLECTION].find(filter_dict).sort("last_active_at", -1)
    items = []
    async for doc in cursor:
        items.append(SessionOut(**doc))
    return items


async def update_session_activity(session_id: str) -> None:
    import time
    await db[COLLECTION].update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {"last_active_at": int(time.time())}},
    )


async def delete_session(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)


async def delete_sessions(filter_dict: dict) -> int:
    result = await db[COLLECTION].delete_many(filter_dict)
    return result.deleted_count
