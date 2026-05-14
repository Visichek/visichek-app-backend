"""Queued media mutations + precompute loaders.

The original blog backend implemented these as ad-hoc celery tasks
named ``celery_worker.create_media_task`` and
``celery_worker.update_media_category_task``. Routes called
``celery_app.send_task(...)`` directly. Here we route them through the
host backend's ``db.write`` dispatcher so they share the same
``queue_job_log`` audit row, ``Idempotency-Key`` flow, and the rest of
the pipeline.

Key insight for performance: the original sync upload endpoints
(``/v1/media/upload-image``, ``/v1/media/upload-video``) blocked the
request thread for the full duration of the R2 PUT / GridFS stream.
Switching to ``enqueue_write`` returns ``202 + job_id`` in <50ms; the
heavy work runs on ``worker-writes`` and the client polls
``/v1/jobs/{job_id}``.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, List

from blog.repositories.media_repo import save_video_to_mongodb_from_bytes
from blog.schemas.media_schema import MediaUpdate
from blog.services.media_service import (
    add_media_from_bytes,
    change_media_category,
    remove_media,
    retrieve_media,
)
from blog.services.r2_upload import upload_image_service_from_bytes
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler

logger = logging.getLogger(__name__)


_MEDIA_INVALIDATIONS = ["media.list"]


def _enqueue_media_list_refresh() -> None:
    try:
        qm = QueueManager.get_instance()
    except RuntimeError:
        return
    for resource in _MEDIA_INVALIDATIONS:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "media_writer: precompute refresh enqueue failed resource=%s",
                resource,
                exc_info=True,
            )


def _decode_b64(value: str) -> bytes:
    """Decode the base64-encoded upload payload sent through the queue.

    The celery broker only accepts JSON-serialisable payloads, so the
    route encodes file bytes with base64 before calling ``enqueue_write``.
    """
    return base64.b64decode(value)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


@write_handler("media.create", invalidates=_MEDIA_INVALIDATIONS)
async def _media_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create a media row from a queued upload.

    Payload schema (built by the route):

    .. code-block:: json

        {
            "media": {"mediaType": "image", "category": "...", "requestUrl": "..."},
            "file_b64": "<base64-encoded bytes>",
            "filename": "...",
            "content_type": "..."
        }
    """
    media_dict = data["media"]
    file_bytes = _decode_b64(data["file_b64"])
    filename = data["filename"]
    content_type = data["content_type"]

    result = await add_media_from_bytes(
        media_dict=media_dict,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        preassigned_id=resource_id,
    )
    _enqueue_media_list_refresh()
    return {
        "id": result.id,
        "url": result.url,
        "mediaType": result.mediaType,
        "category": result.category.value if result.category else None,
    }


@write_handler("media.upload_image", invalidates=_MEDIA_INVALIDATIONS)
async def _media_upload_image(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Upload an image to R2 without creating a media row.

    Used by ``POST /v1/media/upload-image`` and
    ``POST /v1/media/upload-media`` (image branch) — both of which just
    need a hosted URL to embed in editor content.
    """
    file_bytes = _decode_b64(data["file_b64"])
    filename = data.get("filename") or ""
    content_type = data.get("content_type") or "application/octet-stream"
    url = await upload_image_service_from_bytes(
        file_bytes, filename, content_type
    )
    return {"id": resource_id, "url": url}


@write_handler("media.upload_video", invalidates=_MEDIA_INVALIDATIONS)
async def _media_upload_video(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Stream a video into GridFS without creating a media row."""
    file_bytes = _decode_b64(data["file_b64"])
    filename = data.get("filename") or ""
    content_type = data.get("content_type") or "video/mp4"
    request_base_url = (data.get("request_base_url") or "").rstrip("/")
    path = await save_video_to_mongodb_from_bytes(
        file_bytes, filename, content_type
    )
    full_url = (request_base_url + path) if request_base_url else path
    return {"id": resource_id, "url": full_url, "path": path}


@write_handler("media.update_category", invalidates=_MEDIA_INVALIDATIONS)
async def _media_update_category(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    upd = MediaUpdate(**data)
    result = await change_media_category(resource_id, upd)
    _enqueue_media_list_refresh()
    return {
        "id": result.id if result else resource_id,
        "category": result.category.value if result and result.category else None,
    }


@write_handler("media.delete", invalidates=_MEDIA_INVALIDATIONS)
async def _media_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    deleted = await remove_media(resource_id)
    _enqueue_media_list_refresh()
    return {"id": resource_id, "deleted": deleted}


@write_handler(
    "media.append_to_blog",
    invalidates=["blogs.list_admin", "blogs.list_published"] + _MEDIA_INVALIDATIONS,
)
async def _media_append_to_blog(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Upload an image/video and append a BlockNote media block to a blog.

    Mirrors the original ``POST /v1/media/{blog_id}`` endpoint in the
    blog backend. ``resource_id`` is the blog id; the media itself is
    not stored as a separate ``media`` row (consistent with the
    original behaviour).
    """
    from bson import ObjectId
    from blog.repositories.blog_repo import update_blog as update_blog_repo
    from blog.schemas.blog_schema import BlogUpdate
    from blog.services.blog_service import retrieve_blog_by_blog_id
    from blog.services.image_host import generate_media_json

    blog_id = resource_id
    file_bytes = _decode_b64(data["file_b64"])
    filename = data.get("filename") or ""
    content_type = (data.get("content_type") or "").lower()
    caption = data.get("caption") or ""
    request_base_url = (data.get("request_base_url") or "").rstrip("/")

    image_types = {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
    }
    video_types = {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm",
    }

    blog = await retrieve_blog_by_blog_id(blog_id)

    if content_type in image_types:
        url = await upload_image_service_from_bytes(
            file_bytes, filename, content_type
        )
        block = generate_media_json(file_url=url, caption=caption, media_type="image")
    elif content_type in video_types:
        path = await save_video_to_mongodb_from_bytes(
            file_bytes, filename, content_type
        )
        url = (request_base_url + path) if request_base_url else path
        block = generate_media_json(file_url=url, caption=caption, media_type="video")
    else:
        return {
            "id": blog_id,
            "appended": False,
            "error": f"Unsupported file type: {content_type}",
        }

    new_body = list(blog.currentPageBody or [])
    new_body.append(block)
    upd = BlogUpdate(currentPageBody=new_body)
    await update_blog_repo({"_id": ObjectId(blog_id)}, upd)

    _enqueue_media_list_refresh()
    return {"id": blog_id, "appended": True, "url": url, "block": block}


# ---------------------------------------------------------------------------
# Precompute
# ---------------------------------------------------------------------------


@register_precompute("media.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_media_list(_scope: str) -> List[dict[str, Any]]:
    """First page (100 rows) of all media, newest first."""
    rows = await retrieve_media(
        filters=None,
        start=0,
        stop=100,
        sort_field="date_created",
        sort_order=-1,
    )
    return [r.model_dump(mode="json", by_alias=True) for r in rows]
