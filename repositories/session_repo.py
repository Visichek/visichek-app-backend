from __future__ import annotations

from bson import ObjectId
from pymongo import ReturnDocument
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


async def rotate_session_access_token(
    old_access_token_id: str,
    new_access_token_id: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    location: Optional[str] = None,
) -> Optional[SessionOut]:
    """Re-point an existing session row at a freshly minted access token.

    Called on token refresh: the device keeps the same session, only its
    backing access-token id rotates. Bumps ``last_active_at`` and refreshes
    ip/user-agent (and geo location, when resolved) in the same write.
    Returns the updated row, or ``None`` when no session matches
    ``old_access_token_id`` (caller then inserts a new row — e.g. sessions
    issued before this rotation logic landed).
    """
    import time

    set_fields: dict = {
        "access_token_id": new_access_token_id,
        "last_active_at": int(time.time()),
    }
    if ip_address is not None:
        set_fields["ip_address"] = ip_address
    if user_agent is not None:
        set_fields["user_agent"] = user_agent
    if location is not None:
        set_fields["location"] = location

    doc = await db[COLLECTION].find_one_and_update(
        {"access_token_id": old_access_token_id},
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        return SessionOut(**doc)
    return None


async def touch_session_by_token(access_token_id: str) -> None:
    """Bump ``last_active_at`` for the session matching this access token.

    Called from the auth hot path so the sessions list reflects the real
    "last active" time without a separate heartbeat endpoint. Silently
    no-ops when no session row matches (e.g. tokens issued before the
    session-recording fix landed).
    """
    import time

    await db[COLLECTION].update_one(
        {"access_token_id": access_token_id},
        {"$set": {"last_active_at": int(time.time())}},
    )


async def delete_session(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)


async def delete_sessions(filter_dict: dict) -> int:
    result = await db[COLLECTION].delete_many(filter_dict)
    return result.deleted_count
