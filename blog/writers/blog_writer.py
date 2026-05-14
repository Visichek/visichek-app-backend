"""Queued blog mutations + precompute loaders.

Routes call ``enqueue_write(writer_key="blog.create" | "blog.update" |
"blog.delete", ...)`` and return ``202 + job_id`` immediately. The
celery ``worker-writes`` queue then dispatches to one of the
``@write_handler`` functions here.

Two precompute resources are registered:

* ``blogs.list_published`` — first page of the public-website
  paginated list (cached globally). Drives ``GET /api/v1/articles/content/``.
* ``blogs.list_admin``     — first page of the admin paginated list
  (cached globally). Drives ``GET /v1/blogs/``.

Both invalidate on any blog mutation so admin edits surface
immediately on the public site after the worker commits (≈ 1 second
end-to-end on the happy path).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from blog.schemas.blog_schema import (
    BlogCreate,
    BlogOutLessDetail,
    BlogOutLessDetailUserVersion,
    BlogUpdate,
)
from blog.schemas.imports import BlogStatus
from blog.services.blog_service import (
    add_blog,
    remove_blog,
    retrieve_blogs,
    update_blog_by_id,
)
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BLOG_INVALIDATIONS = [
    "blogs.list_admin",
    "blogs.list_published",
]


def _enqueue_blog_list_refresh() -> None:
    """Best-effort refresh of the precompute cache.

    The worker invalidates the cache on commit (``invalidates=[...]``
    drops the key) — this just nudges the precompute worker to fill it
    again instead of waiting for the 60-second fanout.
    """
    qm: Optional[QueueManager] = None
    try:
        qm = QueueManager.get_instance()
    except RuntimeError:
        return
    for resource in _BLOG_INVALIDATIONS:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "blog_writer: precompute refresh enqueue failed resource=%s",
                resource,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


@write_handler("blog.create", invalidates=_BLOG_INVALIDATIONS)
async def _blog_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    blog_data = BlogCreate(**data)
    result = await add_blog(blog_data, preassigned_id=resource_id)
    _enqueue_blog_list_refresh()
    return {
        "id": result.id,
        "title": result.title,
        "slug": result.slug,
        "state": result.state.value if result.state else None,
    }


@write_handler("blog.update", invalidates=_BLOG_INVALIDATIONS)
async def _blog_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    upd = BlogUpdate(**data)
    result = await update_blog_by_id(blog_id=resource_id, blog_data=upd)
    _enqueue_blog_list_refresh()
    return {
        "id": result.id if result else resource_id,
        "title": result.title if result else None,
        "slug": result.slug if result else None,
        "state": result.state.value if result and result.state else None,
    }


@write_handler("blog.delete", invalidates=_BLOG_INVALIDATIONS)
async def _blog_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await remove_blog(resource_id)
    _enqueue_blog_list_refresh()
    return {"id": resource_id, "deleted": True}


# ---------------------------------------------------------------------------
# Precompute loaders
# ---------------------------------------------------------------------------


@register_precompute("blogs.list_published", scope=PrecomputeScope.GLOBAL)
async def _precompute_blogs_published(_scope: str) -> List[dict[str, Any]]:
    """First page (100 rows) of published blogs, newest first.

    Serialised as ``BlogOutLessDetailUserVersion`` so the public website
    can consume the cached payload without further transformation.
    """
    rows = await retrieve_blogs(
        filters={"state": BlogStatus.published.value},
        start=0,
        stop=100,
        sort_field="date_created",
        sort_order=-1,
    )
    out: List[dict[str, Any]] = []
    for row in rows:
        # Hop through BlogOutLessDetail → public-facing shape so the
        # cached payload matches the response model.
        if isinstance(row, BlogOutLessDetail):
            public = BlogOutLessDetailUserVersion.model_validate(
                row.model_dump(by_alias=False)
            )
            out.append(public.model_dump(mode="json", by_alias=True))
        else:
            out.append(row)  # type: ignore[arg-type]
    return out


@register_precompute("blogs.list_admin", scope=PrecomputeScope.GLOBAL)
async def _precompute_blogs_admin(_scope: str) -> List[dict[str, Any]]:
    """First page (100 rows) of every blog (draft + published) for admins."""
    rows = await retrieve_blogs(
        filters=None,
        start=0,
        stop=100,
        sort_field="date_created",
        sort_order=-1,
    )
    return [r.model_dump(mode="json", by_alias=True) for r in rows]
