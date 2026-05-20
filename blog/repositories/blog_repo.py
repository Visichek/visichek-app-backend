"""Blog repository — pure Mongo access, no business logic.

Ported from ``visichek-blog-backend/repositories/blog.py``. Key changes:

* All inserts accept ``preassigned_id: Optional[str] = None`` so the
  queued-write pipeline can route mutations through
  ``enqueue_write`` with a stable pre-generated id (CLAUDE.md / queue
  conventions).
* Uses the host backend's ``core.database.db`` singleton (works with
  both MongoDB Motor and the SQLite shim).
* No service-level fall-back image logic — that lives in the service
  layer.
"""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ReturnDocument

from blog.schemas.blog_schema import BlogCreate, BlogOut, BlogOutLessDetail, BlogUpdate
from core.database import db

COLLECTION = "blogs"


async def create_blog(
    blog_data: BlogCreate, *, preassigned_id: Optional[str] = None
) -> BlogOut:
    """Insert a blog. If ``preassigned_id`` is provided the queued-write
    pipeline pre-generated the id; we reuse it as ``_id`` so subsequent
    polling and reads find the doc immediately.
    """
    blog_dict = blog_data.model_dump()
    if preassigned_id:
        try:
            blog_dict["_id"] = ObjectId(preassigned_id)
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid preassigned blog id: {exc}",
            )
    result = await db[COLLECTION].insert_one(blog_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Blog insert succeeded but doc not found",
        )
    return BlogOut(**doc)


async def get_blog(filter_dict: dict) -> Optional[BlogOut]:
    try:
        result = await db[COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return BlogOut(**result)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching blog: {exc}",
        )


async def get_blogs(
    filter_dict: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,  # 1 ascending, -1 descending
) -> List[BlogOutLessDetail]:
    """Paginated blog list. Returns a list of ``BlogOutLessDetail``."""
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

        blog_list: List[BlogOutLessDetail] = []
        item_index = 1
        async for doc in cursor:
            entry = BlogOutLessDetail(**doc)
            entry.totalItems = total
            entry.itemIndex = item_index
            blog_list.append(entry)
            item_index += 1

        return blog_list
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching blogs: {exc}",
        )


async def update_blog(filter_dict: dict, blog_data: BlogUpdate) -> Optional[BlogOut]:
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": blog_data.model_dump(exclude_none=True)},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return BlogOut(**result)


async def delete_blog(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)


# ---------------------------------------------------------------------------
# Public-site search (originally in sub_app1/repository/blog.py)
# ---------------------------------------------------------------------------


async def search_blogs_repo(filters: dict, skip: int, limit: int):
    """Execute a Mongo ``find`` for the website's search UI.

    Imported here rather than in a separate module so the search path
    benefits from the same ``COLLECTION`` constant and lets the
    precompute loader live alongside other blog reads.
    """
    from blog.schemas.blog_schema import BlogOutLessDetailUserVersion

    try:
        cursor = db[COLLECTION].find(filters).skip(skip).limit(limit)
        results: List[BlogOutLessDetailUserVersion] = []
        item_index = skip + 1
        async for doc in cursor:
            item = BlogOutLessDetailUserVersion(**doc)
            item.itemIndex = item_index
            results.append(item)
            item_index += 1
        return results
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while searching blogs: {exc}",
        )
