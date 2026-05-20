"""Blog business logic — orchestrates repo + Unsplash fallback.

Ported from ``visichek-blog-backend/services/blog_service.py``. Public
functions:

* ``add_blog``               — used by the queued ``blog.create`` writer.
* ``update_blog_by_id``      — used by ``blog.update``.
* ``remove_blog``            — used by ``blog.delete``.
* ``retrieve_blog_by_blog_id`` and ``retrieve_blogs`` — read paths used
  directly by routes and by the precompute loaders.

Routes never call these directly for mutations; they call
``enqueue_write`` and the writer module dispatches here.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException

from blog.repositories.blog_repo import (
    create_blog,
    delete_blog,
    get_blog,
    get_blogs,
    update_blog,
)
from blog.schemas.blog_schema import (
    BlogCreate,
    BlogOut,
    BlogOutLessDetail,
    BlogUpdate,
)
from blog.schemas.imports import MediaAsset
from blog.services.unsplash import resolve_feature_image_url
from core.database import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature-image fallback (preserved 1:1 from blog backend)
# ---------------------------------------------------------------------------


def _needs_fallback(feature_image) -> bool:
    if feature_image is None:
        return True
    url = getattr(feature_image, "url", None) or ""
    return not url.strip().lower().startswith("https://")


async def _ensure_feature_image(blog: Optional[BlogOut]) -> Optional[BlogOut]:
    """Backfill the feature image and persist if missing/invalid."""
    if blog is None or not _needs_fallback(blog.featureImage):
        return blog
    fallback_url = await resolve_feature_image_url(blog.title)
    existing_alt = blog.featureImage.altText if blog.featureImage else None
    existing_credit = blog.featureImage.credit if blog.featureImage else None
    blog.featureImage = MediaAsset(
        url=fallback_url,
        altText=existing_alt or blog.title,
        credit=existing_credit or "Image via Unsplash",
    )
    try:
        if getattr(blog, "id", None):
            await db.blogs.update_one(
                {"_id": ObjectId(blog.id)},
                {"$set": {"featureImage": blog.featureImage.model_dump()}},
            )
    except Exception:
        logger.debug("Unable to persist Unsplash fallback image", exc_info=True)
    return blog


# ---------------------------------------------------------------------------
# Mutations — called from the queued writer
# ---------------------------------------------------------------------------


async def add_blog(
    blog_data: BlogCreate, *, preassigned_id: Optional[str] = None
) -> BlogOut:
    """Create a blog. Falls back to Unsplash when no feature image was set."""
    if _needs_fallback(blog_data.featureImage):
        fallback_url = await resolve_feature_image_url(blog_data.title)
        existing_alt = (
            blog_data.featureImage.altText if blog_data.featureImage else None
        )
        existing_credit = (
            blog_data.featureImage.credit if blog_data.featureImage else None
        )
        blog_data.featureImage = MediaAsset(
            url=fallback_url,
            altText=existing_alt or blog_data.title,
            credit=existing_credit or "Image via Unsplash",
        )
    return await create_blog(blog_data, preassigned_id=preassigned_id)


async def remove_blog(blog_id: str) -> bool:
    if not ObjectId.is_valid(blog_id):
        raise HTTPException(status_code=400, detail="Invalid blog ID format")
    result = await delete_blog({"_id": ObjectId(blog_id)})
    if getattr(result, "deleted_count", 0) == 0:
        raise HTTPException(status_code=404, detail="Blog not found")
    return True


async def update_blog_by_id(blog_id: str, blog_data: BlogUpdate) -> Optional[BlogOut]:
    if not ObjectId.is_valid(blog_id):
        raise HTTPException(status_code=400, detail="Invalid blog ID format")

    if blog_data.featureImage is not None and _needs_fallback(blog_data.featureImage):
        existing = await get_blog({"_id": ObjectId(blog_id)})
        title = (
            blog_data.title if blog_data.title else (existing.title if existing else "")
        ) or ""
        fallback_url = await resolve_feature_image_url(title)
        blog_data.featureImage = MediaAsset(
            url=fallback_url,
            altText=blog_data.featureImage.altText or title,
            credit=blog_data.featureImage.credit or "Image via Unsplash",
        )

    result = await update_blog({"_id": ObjectId(blog_id)}, blog_data)
    if not result:
        raise HTTPException(status_code=404, detail="Blog not found or update failed")
    await _ensure_feature_image(result)
    return result


# ---------------------------------------------------------------------------
# Reads — called directly from routes / precompute loaders
# ---------------------------------------------------------------------------


async def retrieve_blog_by_blog_id(id: str) -> BlogOut:
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid blog ID format")

    result = await get_blog({"_id": ObjectId(id)})
    if not result:
        raise HTTPException(status_code=404, detail="Blog not found")

    await _ensure_feature_image(result)
    return result


async def retrieve_blogs(
    filters: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> List[BlogOutLessDetail]:
    """Paginated blog list with optional sort/filter. Fills Unsplash
    fallback on each row before returning."""

    clean_filters: Optional[dict] = filters
    if filters == {"field": "value"}:  # legacy sentinel from old frontends
        clean_filters = None

    if clean_filters and sort_field and sort_order:
        results = await get_blogs(
            filter_dict=clean_filters,
            start=start,
            stop=stop,
            sort_field=sort_field,
            sort_order=sort_order,
        )
    elif clean_filters:
        results = await get_blogs(filter_dict=clean_filters, start=start, stop=stop)
    elif sort_field and sort_order:
        results = await get_blogs(
            start=start, stop=stop, sort_field=sort_field, sort_order=sort_order
        )
    else:
        results = await get_blogs(start=start, stop=stop)

    for blog in results:
        await _ensure_feature_image(blog)  # type: ignore[arg-type]
    return results
