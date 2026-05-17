"""Admin media routes — ``/v1/media/*``.

Listing style follows the host backend's tables contract — see
``BLOGS_LIST_SPEC`` in ``admin_blog_route.py`` for the same shape.

Uploads stream directly to R2 (or local disk fallback) from the route
handler using boto3 multipart transfer. The frontend contract is
preserved — the response shape is still ``{ id, job_id, status }`` and
clients can still poll ``/v1/jobs/{job_id}`` — but the row is written
in ``succeeded`` state synchronously so the first poll resolves
immediately. We do NOT route upload bytes through the celery broker:
that path required base64-encoding the file into a JSON payload, which
blows up Redis size limits and worker memory on anything over ~5 MB.

Non-upload mutations (update_category / delete) keep the queued path
because they ship only small JSON.
"""

from __future__ import annotations

from typing import Any, List, Literal

from bson import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)

from blog.schemas.imports import CategoryNameEnum
from blog.schemas.media_schema import MediaBase, MediaOut, MediaUpdate
from blog.services.media_service import (
    add_media_from_stream,
    retrieve_media_by_id,
)
from blog.services.r2_upload import upload_media_stream
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write, record_inline_completed_write
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/media", tags=["Blog Media"])

_PRECOMPUTE_TTL = 300


# ---------------------------------------------------------------------------
# ListSpec
# ---------------------------------------------------------------------------


# Build allowed_values dynamically from the enum so adding categories doesn't
# require touching this list.
_CATEGORY_NAME_VALUES: frozenset[str] = frozenset(c.value for c in CategoryNameEnum)


MEDIA_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "last_updated", "name", "category", "mediaType"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("name", "category"),
    filters={
        "mediaType": FilterDef(
            name="mediaType",
            multi=True,
            allowed_values=frozenset({"image", "video"}),
        ),
        "category": FilterDef(
            name="category",
            multi=True,
            allowed_values=_CATEGORY_NAME_VALUES,
        ),
    },
    facet_fields=frozenset({"mediaType", "category"}),
    range_filters={"createdAt": "date_created", "updatedAt": "last_updated"},
)


def _map_media_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


def _is_default_admin_media_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(MEDIA_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(MEDIA_LIST_SPEC.default_limit)


def _is_image_content_type(content_type: str) -> bool:
    return content_type.startswith("image/")


def _is_video_content_type(content_type: str) -> bool:
    return content_type.startswith("video/")


# ---------------------------------------------------------------------------
# Upload endpoints — queued
# ---------------------------------------------------------------------------


async def _stream_upload_or_400(
    file: UploadFile, default_content_type: str = "application/octet-stream"
) -> tuple[str, str, str]:
    """Stream ``file`` to R2 and return ``(url, filename, content_type)``.

    Raises 400 on an unsupported content type so callers can mirror the
    original error contract. Closes the upload's underlying tempfile in
    a ``finally`` block — Starlette doesn't auto-close ``UploadFile``.
    """
    content_type = (file.content_type or default_content_type).lower()
    filename = file.filename or ""
    try:
        url = await upload_media_stream(file.file, filename, content_type)
    finally:
        await file.close()
    return url, filename, content_type


@router.post("/upload-media", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Upload queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Upload an image or video. The route detects the file type from its "
        "MIME header and streams the body straight to R2 (or local disk when "
        "R2 isn't configured) without buffering in memory. Returns the same "
        "``{ id, job_id, status }`` envelope as a queued write; the job row "
        "is written in ``succeeded`` state synchronously so the first poll of "
        "``/v1/jobs/{job_id}`` resolves immediately with the final URL."
    ),
    summary="Upload media (streaming)",
)
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    content_type = (file.content_type or "").lower()
    if not (_is_image_content_type(content_type) or _is_video_content_type(content_type)):
        await file.close()
        raise HTTPException(
            status_code=400, detail=f"Unsupported file type: {content_type}"
        )

    writer_key = "media.upload_image" if _is_image_content_type(content_type) else "media.upload_video"
    url, filename, ctype = await _stream_upload_or_400(
        file, "video/mp4" if _is_video_content_type(content_type) else "application/octet-stream"
    )
    return await record_inline_completed_write(
        writer_key=writer_key,
        payload={"filename": filename, "content_type": ctype},
        resource_type="media_upload",
        result={"url": url},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Media create queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Upload media and create a `media` row in one operation. The upload "
        "streams directly to R2 from the route — no celery hop — and the row "
        "is persisted inline. Response shape matches a queued write."
    ),
    summary="Create media with category (streaming)",
)
async def upload_media_with_category(
    request: Request,
    category: CategoryNameEnum = Form(...),
    file: UploadFile = File(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    content_type = (file.content_type or "").lower()
    if not (_is_image_content_type(content_type) or _is_video_content_type(content_type)):
        await file.close()
        raise HTTPException(
            status_code=400, detail=f"Unsupported file type: {content_type}"
        )

    media_type: Literal["image", "video"] = (
        "image" if _is_image_content_type(content_type) else "video"
    )
    media_dict = MediaBase(
        mediaType=media_type,
        category=category,
        requestUrl=str(request.base_url).rstrip("/") if media_type == "video" else None,
    ).model_dump()

    filename = file.filename or ""
    try:
        media = await add_media_from_stream(
            media_dict=media_dict,
            fileobj=file.file,
            filename=filename,
            content_type=content_type,
        )
    finally:
        await file.close()

    return await record_inline_completed_write(
        writer_key="media.create",
        payload={
            "media": media_dict,
            "filename": filename,
            "content_type": content_type,
        },
        resource_type="media",
        resource_id=media.id,
        result={
            "id": media.id,
            "url": media.url,
            "mediaType": media.mediaType,
            "category": media.category.value if media.category else None,
        },
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/upload-image", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Image upload queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload image (streaming)",
)
async def upload_image_endpoint(
    request: Request,
    file: UploadFile = File(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    url, filename, ctype = await _stream_upload_or_400(file)
    return await record_inline_completed_write(
        writer_key="media.upload_image",
        payload={"filename": filename, "content_type": ctype},
        resource_type="media_upload",
        result={"url": url},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/upload-video", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Video upload queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload video (streaming)",
)
async def upload_video_endpoint(
    request: Request,
    file: UploadFile = File(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    url, filename, ctype = await _stream_upload_or_400(file, "video/mp4")
    return await record_inline_completed_write(
        writer_key="media.upload_video",
        payload={"filename": filename, "content_type": ctype},
        resource_type="media_upload",
        result={"url": url},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Media retrieved",
    description=(
        "Paginated media list. Supports ``skip``/``limit``, ``sort``, ``q``, "
        "filters ``mediaType`` and ``category`` (both multi), and ``facets``. "
        "The unfiltered first page is served from the precompute cache."
    ),
    summary="List media",
    include_meta=True,
)
async def list_media(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_admin_media_listing(request):
        async def _loader():
            cursor = (
                db.media.find({})
                .sort([("date_created", -1)])
                .skip(0)
                .limit(MEDIA_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_map_media_doc(doc))
            total = await db.media.count_documents({})
            return {"items": items, "total": total}

        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="media.list",
            ttl=_PRECOMPUTE_TTL,
            loader=_loader,
        )
        if isinstance(cached, dict):
            items = cached.get("items", []) or []
            total = cached.get("total", len(items))
        elif isinstance(cached, list):
            items = cached
            total = len(items)
        else:
            items, total = [], 0
        return {
            "items": items,
            "meta": {
                "total": total,
                "skip": 0,
                "limit": MEDIA_LIST_SPEC.default_limit,
                "hasMore": total > MEDIA_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        map_doc=_map_media_doc,
    )


@router.get("/recent")
@document_response(
    message="Recent media retrieved",
    description=(
        "Shortcut for ``GET /v1/media?sort=-date_created&limit=50``. "
        "Accepts the same query params as the main list endpoint."
    ),
    summary="List most-recent media (shortcut)",
    include_meta=True,
)
async def list_recent_media(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    query = parse_list_query(request, MEDIA_LIST_SPEC)
    if request.query_params.get("limit") is None:
        query.limit = 50
    return await run_list(
        collection=db.media,
        query=query,
        map_doc=_map_media_doc,
    )


@router.get("/by-type/{media_type}")
@document_response(
    message="Media listed by type",
    description=(
        "Shortcut: locks the ``mediaType`` filter to the path value. Same "
        "supported query params as ``GET /v1/media``."
    ),
    summary="List media by type (shortcut)",
    include_meta=True,
)
async def list_media_by_type(
    request: Request,
    media_type: str = Path(..., description="'image' or 'video'"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if media_type not in {"image", "video"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media_type: {media_type}",
        )
    query = parse_list_query(request, MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        base_filter={"mediaType": media_type},
        map_doc=_map_media_doc,
    )


@router.get("/by-category/{category}")
@document_response(
    message="Media listed by category",
    description=(
        "Shortcut: locks the ``category`` filter to the path value. Same "
        "supported query params as ``GET /v1/media``."
    ),
    summary="List media by category (shortcut)",
    include_meta=True,
)
async def list_media_by_category(
    request: Request,
    category: str = Path(..., description="Category display name"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    query = parse_list_query(request, MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        base_filter={"category": category},
        map_doc=_map_media_doc,
    )


# ---------------------------------------------------------------------------
# Single item + mutations
# ---------------------------------------------------------------------------


@router.get("/{media_id}")
@document_response(
    message="Media retrieved",
    summary="Get media by id",
)
async def get_media_by_id_endpoint(
    media_id: str = Path(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> MediaOut:
    item = await retrieve_media_by_id(media_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found"
        )
    return item


@router.patch("/{media_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Media category update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update media category (queued)",
)
async def update_media_category_endpoint(
    payload: MediaUpdate,
    request: Request,
    media_id: str = Path(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(media_id):
        raise HTTPException(status_code=400, detail="Invalid media id format")
    return await enqueue_write(
        writer_key="media.update_category",
        payload=payload.model_dump(mode="json"),
        resource_type="media",
        resource_id=media_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{media_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Media deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete media (queued)",
)
async def delete_media_endpoint(
    request: Request,
    media_id: str = Path(...),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    return await enqueue_write(
        writer_key="media.delete",
        payload={},
        resource_type="media",
        resource_id=media_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Append media block to a blog — preserved verbatim from blog backend
# ---------------------------------------------------------------------------


@router.post("/{blog_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Media append queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Upload an image or video and append a BlockNote media block to the "
        "blog's ``currentPageBody``. The file streams to R2 directly from the "
        "route; the blog update runs inline. Returns ``202 + job_id`` matching "
        "the queued-write contract — the job row is in ``succeeded`` state on "
        "first poll."
    ),
    summary="Append media block to blog (streaming)",
)
async def append_media_to_blog(
    request: Request,
    blog_id: str = Path(..., description="Blog id"),
    caption: str = Form(..., description="Caption for the embedded block"),
    file: UploadFile = File(..., description="Image or video file"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(blog_id):
        await file.close()
        raise HTTPException(status_code=400, detail="Invalid blog id format")

    content_type = (file.content_type or "").lower()
    image_types = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
    video_types = {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm",
    }
    media_type: Literal["image", "video"]
    if content_type in image_types:
        media_type = "image"
    elif content_type in video_types:
        media_type = "video"
    else:
        await file.close()
        raise HTTPException(
            status_code=400, detail=f"Unsupported file type: {content_type}"
        )

    filename = file.filename or ""
    try:
        url = await upload_media_stream(file.file, filename, content_type)
    finally:
        await file.close()

    from blog.repositories.blog_repo import update_blog as update_blog_repo
    from blog.schemas.blog_schema import BlogUpdate
    from blog.services.blog_service import retrieve_blog_by_blog_id
    from blog.services.image_host import generate_media_json

    blog = await retrieve_blog_by_blog_id(blog_id)
    block = generate_media_json(file_url=url, caption=caption, media_type=media_type)
    new_body = list(blog.currentPageBody or [])
    new_body.append(block)
    await update_blog_repo(
        {"_id": ObjectId(blog_id)}, BlogUpdate(currentPageBody=new_body)
    )

    return await record_inline_completed_write(
        writer_key="media.append_to_blog",
        payload={
            "filename": filename,
            "content_type": content_type,
            "caption": caption,
        },
        resource_type="blog",
        resource_id=blog_id,
        result={"id": blog_id, "url": url, "appended": True, "block": block},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
