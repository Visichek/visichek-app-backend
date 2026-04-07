from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from core.database import db
from schemas.branch_schema import BranchCreate, BranchOut, BranchUpdate

COLLECTION = "branches"


async def create_branch(schema: BranchCreate) -> BranchOut:
    """Insert a new branch document."""
    data = schema.model_dump()
    result = await db[COLLECTION].insert_one(data)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return BranchOut(**doc)


async def get_branch(filter_dict: dict) -> Optional[BranchOut]:
    """Fetch a single branch matching the filter."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return BranchOut(**doc)


async def get_branches(
    filter_dict: dict | None = None,
    start: int = 0,
    stop: int = 100,
) -> List[BranchOut]:
    """Fetch multiple branches matching the filter."""
    if filter_dict is None:
        filter_dict = {}
    cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop)
    docs = await cursor.to_list(length=stop)
    return [BranchOut(**doc) for doc in docs]


async def count_branches(filter_dict: dict | None = None) -> int:
    """Count branches matching the filter."""
    if filter_dict is None:
        filter_dict = {}
    return await db[COLLECTION].count_documents(filter_dict)


async def update_branch(filter_dict: dict, schema: BranchUpdate) -> Optional[BranchOut]:
    """Update a branch document, returning the updated document."""
    update_data = {k: v for k, v in schema.model_dump().items() if v is not None}
    if not update_data:
        return await get_branch(filter_dict)
    await db[COLLECTION].update_one(filter_dict, {"$set": update_data})
    return await get_branch(filter_dict)


async def delete_branch(filter_dict: dict) -> bool:
    """Delete a branch document."""
    result = await db[COLLECTION].delete_one(filter_dict)
    return result.deleted_count > 0
