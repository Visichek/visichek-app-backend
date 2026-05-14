"""Media repository — Mongo + GridFS access for blog media.

Ported from ``visichek-blog-backend/repositories/media_host.py``. The
GridFS bucket is lazily resolved via ``_gridfs_bucket()`` so it picks
up the host backend's ``core.database.db`` (which may be Motor or
SQLite) at call time rather than at import time. ``create_media``
accepts a ``preassigned_id`` for queued-write integration.
"""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pymongo import ReturnDocument

from blog.schemas.media_schema import MediaCreate, MediaOut, MediaUpdate
from core.database import db

COLLECTION = "media"


def _gridfs_bucket() -> AsyncIOMotorGridFSBucket:
    """Lazily build a GridFS bucket against the active database.

    Resolving at call time (instead of module import) avoids a hard
    dependency on Motor being initialised before the celery worker
    imports this module. The ``db`` singleton is a thin wrapper around
    Motor's ``AgnosticDatabase`` (or the SQLite shim in dev) — we cast
    here because the wrapper doesn't expose Motor's full protocol.
    """
    return AsyncIOMotorGridFSBucket(db)  # type: ignore[arg-type]


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


# ---------------------------------------------------------------------------
# GridFS video helpers
# ---------------------------------------------------------------------------


async def save_video_to_mongodb(file: UploadFile) -> str:
    """Stream an ``UploadFile`` into GridFS. Returns ``/videos/{id}``."""
    fs = _gridfs_bucket()
    upload_stream = fs.open_upload_stream(
        file.filename or "video.bin",
        metadata={"content_type": file.content_type},
    )

    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        await upload_stream.write(chunk)

    await upload_stream.close()
    video_id = upload_stream._id  # noqa: SLF001 — GridFS API surface
    return f"/videos/{str(video_id)}"


async def save_video_to_mongodb_from_bytes(
    file_bytes: bytes, filename: str, content_type: str
) -> str:
    """Same as ``save_video_to_mongodb`` but accepts raw bytes (used from
    celery workers where ``UploadFile`` isn't available)."""
    fs = _gridfs_bucket()
    upload_stream = fs.open_upload_stream(
        filename or "video.bin",
        metadata={"content_type": content_type},
    )

    chunk_size = 1024 * 1024  # 1 MB
    for i in range(0, len(file_bytes), chunk_size):
        await upload_stream.write(file_bytes[i : i + chunk_size])

    await upload_stream.close()
    video_id = upload_stream._id  # noqa: SLF001
    return f"/videos/{str(video_id)}"


async def open_video_stream(video_id: str):
    """Open a GridFS download stream for the given video id.

    Returns the stream object on success, or raises ``HTTPException``
    (404) if the id is invalid or the file doesn't exist.
    """
    try:
        oid = ObjectId(video_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Video not found")

    fs = _gridfs_bucket()
    try:
        return await fs.open_download_stream(oid)
    except Exception:
        raise HTTPException(status_code=404, detail="Video not found")
