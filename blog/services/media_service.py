"""Media business logic — composes repo + storage layer.

Heavy work (R2 upload, local-disk writes) happens on the celery
``worker-writes`` queue via ``blog.writers.media_writer``. These
functions are the implementation those writers call.

Read paths (``retrieve_media``, ``retrieve_media_by_id``) are used by
routes and precompute loaders directly — no queue round-trip.
"""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException

from blog.repositories.media_repo import (
    create_media,
    delete_media,
    get_media,
    get_media_files,
    update_media_category,
)
from blog.schemas.media_schema import (
    MediaBase,
    MediaCreate,
    MediaOut,
    MediaUpdate,
)
from blog.services.r2_upload import upload_media_bytes


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def retrieve_media(
    filters: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> List[MediaOut]:
    return await get_media_files(
        filter_dict=filters,
        start=start,
        stop=stop,
        sort_field=sort_field,
        sort_order=sort_order,
    )


async def retrieve_media_by_id(media_id: str) -> Optional[MediaOut]:
    query: dict
    if ObjectId.is_valid(media_id):
        query = {"_id": ObjectId(media_id)}
    else:
        query = {"_id": media_id}
    return await get_media(filter_dict=query)


# ---------------------------------------------------------------------------
# Mutations (called by writers)
# ---------------------------------------------------------------------------


async def add_media_from_bytes(
    media_dict: dict,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    *,
    preassigned_id: Optional[str] = None,
) -> MediaOut:
    """Upload bytes to R2 (or local fallback), then insert the media row.

    Both images and videos go through the same upload path.
    """
    media = MediaBase(**media_dict)
    if media.mediaType not in ("image", "video"):  # pragma: no cover
        raise HTTPException(
            status_code=400, detail=f"Unsupported mediaType: {media.mediaType}"
        )

    url = await upload_media_bytes(file_bytes, filename, content_type)
    media_data = MediaCreate(**media_dict, url=url, name=filename)
    return await create_media(media_data, preassigned_id=preassigned_id)


async def change_media_category(
    media_id: str, update: MediaUpdate
) -> Optional[MediaOut]:
    if not ObjectId.is_valid(media_id):
        raise HTTPException(status_code=400, detail="Invalid media ID format")
    return await update_media_category(
        {"_id": ObjectId(media_id)}, update
    )


async def remove_media(media_id: str) -> bool:
    query: dict
    if ObjectId.is_valid(media_id):
        query = {"_id": ObjectId(media_id)}
    else:
        query = {"_id": media_id}
    result = await delete_media(query)
    return bool(getattr(result, "deleted_count", 0))
