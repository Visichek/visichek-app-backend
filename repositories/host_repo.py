from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.host_schema import HostCreate, HostOut, HostUpdate

COLLECTION = "hosts"


async def create_host(
    schema: HostCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> HostOut:
    """Insert a new host document."""
    data = schema.model_dump()
    if preassigned_id:
        data["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(data)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return HostOut(**doc)


async def get_host(filter_dict: dict) -> Optional[HostOut]:
    """Fetch a single host matching the filter."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return HostOut(**doc)


async def get_hosts(
    filter_dict: dict | None = None,
    start: int = 0,
    stop: int = 100,
) -> List[HostOut]:
    """Fetch multiple hosts matching the filter."""
    if filter_dict is None:
        filter_dict = {}
    cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop - start)
    docs = await cursor.to_list(length=stop - start)
    return [HostOut(**doc) for doc in docs]


async def count_hosts(filter_dict: dict | None = None) -> int:
    """Count hosts matching the filter."""
    if filter_dict is None:
        filter_dict = {}
    return await db[COLLECTION].count_documents(filter_dict)


async def update_host(filter_dict: dict, schema: HostUpdate) -> Optional[HostOut]:
    """Update a host document, returning the updated document."""
    update_data = {k: v for k, v in schema.model_dump().items() if v is not None}
    if not update_data:
        return await get_host(filter_dict)
    doc = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    return HostOut(**doc)


async def delete_host(filter_dict: dict):
    """Delete a host document. Returns the pymongo DeleteResult."""
    return await db[COLLECTION].delete_one(filter_dict)
