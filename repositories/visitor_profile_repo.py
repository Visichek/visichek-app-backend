from __future__ import annotations

import time
from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.visitor_profile_schema import (
    VisitorProfileCreate,
    VisitorProfileUpdate,
    VisitorProfileOut,
)


async def create_visitor_profile(
    profile_data: VisitorProfileCreate,
) -> VisitorProfileOut:
    profile_dict = profile_data.model_dump()
    result = await db.visitor_profiles.insert_one(profile_dict)
    result = await db.visitor_profiles.find_one({"_id": result.inserted_id})
    return VisitorProfileOut(**result)


async def get_visitor_profile(filter_dict: dict) -> Optional[VisitorProfileOut]:
    try:
        # Always exclude soft-deleted unless explicitly requested
        if "deleted_at" not in filter_dict:
            filter_dict["deleted_at"] = None
        result = await db.visitor_profiles.find_one(filter_dict)
        if result is None:
            return None
        return VisitorProfileOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching visitor profile: {str(e)}",
        )


async def get_visitor_profiles(
    filter_dict: dict = {}, start=0, stop=100
) -> List[VisitorProfileOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        if "deleted_at" not in filter_dict:
            filter_dict["deleted_at"] = None
        cursor = db.visitor_profiles.find(filter_dict).skip(start).limit(stop - start)
        profile_list = []
        async for doc in cursor:
            profile_list.append(VisitorProfileOut(**doc))
        return profile_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching visitor profiles: {str(e)}",
        )


async def get_visitor_profile_by_phone(
    tenant_id: str, phone: str
) -> Optional[VisitorProfileOut]:
    return await get_visitor_profile({"tenant_id": tenant_id, "phone": phone})


async def get_visitor_profile_by_email(
    tenant_id: str, email: str
) -> Optional[VisitorProfileOut]:
    return await get_visitor_profile({"tenant_id": tenant_id, "email_address": email})


async def get_visitor_profile_by_id_number(
    tenant_id: str, id_number: str
) -> Optional[VisitorProfileOut]:
    return await get_visitor_profile({"tenant_id": tenant_id, "id_number": id_number})


async def update_visitor_profile(
    filter_dict: dict, profile_data: VisitorProfileUpdate
) -> VisitorProfileOut:
    update_dict = {k: v for k, v in profile_data.model_dump().items() if v is not None}
    result = await db.visitor_profiles.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return VisitorProfileOut(**result)


async def soft_delete_visitor_profile(filter_dict: dict) -> VisitorProfileOut:
    import time

    result = await db.visitor_profiles.find_one_and_update(
        filter_dict,
        {"$set": {"deleted_at": int(time.time())}},
        return_document=ReturnDocument.AFTER,
    )
    return VisitorProfileOut(**result)


async def get_visitor_profile_including_deleted(
    filter_dict: dict,
) -> Optional[VisitorProfileOut]:
    """Fetch a profile WITHOUT the implicit ``deleted_at: None`` filter.

    Needed by the DSR erasure / restore / purge paths, which operate on
    soft-deleted rows that ``get_visitor_profile`` deliberately hides.
    """
    result = await db.visitor_profiles.find_one(filter_dict)
    if result is None:
        return None
    return VisitorProfileOut(**result)


async def get_scheduled_for_deletion_profiles(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[VisitorProfileOut]:
    """Soft-deleted profiles awaiting permanent deletion, soonest first."""
    filter_dict = {
        "tenant_id": tenant_id,
        "deleted_at": {"$ne": None},
        "scheduled_purge_at": {"$ne": None},
    }
    cursor = (
        db.visitor_profiles.find(filter_dict)
        .sort("scheduled_purge_at", 1)
        .skip(start)
        .limit(stop - start)
    )
    return [VisitorProfileOut(**doc) async for doc in cursor]


async def get_profiles_due_for_purge(cutoff: int) -> List[VisitorProfileOut]:
    """All tenants' profiles whose scheduled purge time has elapsed.

    Driven by the ``run_scheduled_erasure_purge`` APScheduler sweep.
    """
    filter_dict = {
        "deleted_at": {"$ne": None},
        "scheduled_purge_at": {"$ne": None, "$lte": cutoff},
    }
    cursor = db.visitor_profiles.find(filter_dict)
    return [VisitorProfileOut(**doc) async for doc in cursor]


async def schedule_visitor_profile_purge(
    filter_dict: dict, deleted_at: int, scheduled_purge_at: int
) -> Optional[VisitorProfileOut]:
    """Soft-delete a profile and stamp its permanent-deletion due time."""
    result = await db.visitor_profiles.find_one_and_update(
        filter_dict,
        {
            "$set": {
                "deleted_at": deleted_at,
                "scheduled_purge_at": scheduled_purge_at,
                "last_updated": int(time.time()),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return VisitorProfileOut(**result)


async def restore_visitor_profile(filter_dict: dict) -> Optional[VisitorProfileOut]:
    """Clear the soft-delete + scheduled-purge markers (undo an erasure)."""
    result = await db.visitor_profiles.find_one_and_update(
        filter_dict,
        {
            "$set": {
                "deleted_at": None,
                "scheduled_purge_at": None,
                "last_updated": int(time.time()),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return VisitorProfileOut(**result)


async def hard_delete_visitor_profile(filter_dict: dict) -> int:
    """Permanently remove a profile. Returns the deleted document count."""
    result = await db.visitor_profiles.delete_one(filter_dict)
    return result.deleted_count


async def search_visitor_profiles(
    tenant_id: str, query: str, start=0, stop=20
) -> List[VisitorProfileOut]:
    filter_dict = {
        "tenant_id": tenant_id,
        "deleted_at": None,
        "$or": [
            {"full_name": {"$regex": query, "$options": "i"}},
            {"phone": {"$regex": query, "$options": "i"}},
            {"email_address": {"$regex": query, "$options": "i"}},
            {"company": {"$regex": query, "$options": "i"}},
        ],
    }
    cursor = db.visitor_profiles.find(filter_dict).skip(start).limit(stop - start)
    profile_list = []
    async for doc in cursor:
        profile_list.append(VisitorProfileOut(**doc))
    return profile_list


async def increment_visitor_profile_visits(
    filter_dict: dict,
) -> Optional[VisitorProfileOut]:
    """Atomically increment total_visits and set last_visit_date."""
    result = await db.visitor_profiles.find_one_and_update(
        filter_dict,
        {
            "$inc": {"total_visits": 1},
            "$set": {
                "last_visit_date": int(time.time()),
                "last_updated": int(time.time()),
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return VisitorProfileOut(**result)
