"""Media repository — MongoDB access for blog media rows.

Only metadata rows live in Mongo; the actual bytes go to R2 (or local
disk) via ``blog.services.r2_upload``. ``create_media`` accepts a
``preassigned_id`` so queued writes can pre-allocate the document id.
"""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ReturnDocument

from blog.schemas.media_schema import MediaCreate, MediaOut, MediaUpdate
from core.database import db

COLLECTION = "media"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_media(
    media_data: MediaCreate, *, preassigned_id: Optional[str] = None
) -> MediaOut:
    media_dict = media_data.model_dump()
    if preassigned_id:
        try:
            media_dict["_id"] = ObjectId(preassigned_id)
        except Exception as exc:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid preassigned media id: {exc}",
            )
    result = await db[COLLECTION].insert_one(media_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Media insert succeeded but doc not found",
        )
    return MediaOut(**doc)


async def get_media(filter_dict: dict) -> Optional[MediaOut]:
    try:
        result = await db[COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return MediaOut(**result)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching media: {exc}",
        )


async def get_media_files(
    filter_dict: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> List[MediaOut]:
    try:
        if filter_dict is None:
            filter_dict = {}

        cursor = db[COLLECTION].find(filter_dict)
        total = await db[COLLECTION].count_documents(filter_dict)

        if sort_field and sort_order:
            cursor = cursor.sort(sort_field, sort_order)
        else:
            cursor = cursor.sort("date_created", -1)

        cursor = cursor.skip(start).limit(stop - start)

        media_list: List[MediaOut] = []
        item_index = 1
        async for doc in cursor:
            entry = MediaOut(**doc)
            entry.totalItems = total
            entry.itemIndex = item_index
            media_list.append(entry)
            item_index += 1

        return media_list
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching media: {exc}",
        )


async def update_media_category(
    filter_dict: dict, media_data: MediaUpdate
) -> Optional[MediaOut]:
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": media_data.model_dump(exclude_none=True)},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return MediaOut(**result)


async def delete_media(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)
