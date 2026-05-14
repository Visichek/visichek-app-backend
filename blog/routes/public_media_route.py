"""Public-website media reads — ``/api/v1/media/*``.

Listing style follows the host backend's tables contract:
``ListSpec`` + ``parse_list_query`` + ``run_list``. No auth.

Outputs are wrapped in ``MediaOutUser`` (http→https coercion + serialised
ids) so the public website never sees raw Mongo shapes.
"""

from __future__ import annotations

from typing import Any, List, Literal

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Path, Request, status

from blog.schemas.imports import CATEGORY_PAIRS, CategorySlugEnum
from blog.schemas.media_schema import MediaOut, MediaOutUser
from blog.services.media_service import retrieve_media_by_id
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response

router = APIRouter(prefix="/media", tags=["Public Media"])

_PRECOMPUTE_TTL = 300


PUBLIC_MEDIA_LIST_SPEC = ListSpec(
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
        ),
    },
    facet_fields=frozenset({"mediaType", "category"}),
    range_filters={"createdAt": "date_created", "updatedAt": "last_updated"},
)


def _map_public_media_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Apply ``MediaOutUser`` transforms (id stringification + http→https)."""
    return MediaOutUser.model_validate(doc).model_dump(mode="json", by_alias=True)


def _is_default_public_media_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(PUBLIC_MEDIA_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(
        PUBLIC_MEDIA_LIST_SPEC.default_limit
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Media retrieved",
    description=(
        "Paginated public media list. Supports ``skip``/``limit``, "
        "``sort``, ``q``, filters ``mediaType``/``category`` (multi), and "
        "``facets``. Default first page comes from the precompute cache."
    ),
    summary="List public media",
    include_meta=True,
)
async def list_public_media(request: Request) -> Any:
    if _is_default_public_media_listing(request):
        async def _loader():
            cursor = (
                db.media.find({})
                .sort([("date_created", -1)])
                .skip(0)
                .limit(PUBLIC_MEDIA_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_map_public_media_doc(doc))
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
                "limit": PUBLIC_MEDIA_LIST_SPEC.default_limit,
                "hasMore": total > PUBLIC_MEDIA_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, PUBLIC_MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        map_doc=_map_public_media_doc,
    )


@router.get("/recent")
@document_response(
    message="Recent media retrieved",
    description=(
        "Shortcut for ``GET /api/v1/media?sort=-date_created&limit=50``."
    ),
    summary="List recent public media (shortcut)",
    include_meta=True,
)
async def list_recent_public_media(request: Request) -> Any:
    query = parse_list_query(request, PUBLIC_MEDIA_LIST_SPEC)
    if request.query_params.get("limit") is None:
        query.limit = 50
    return await run_list(
        collection=db.media,
        query=query,
        map_doc=_map_public_media_doc,
    )


@router.get("/by-type/{media_type}")
@document_response(
    message="Media listed by type",
    summary="List public media by type (shortcut)",
    include_meta=True,
)
async def list_media_by_type(
    request: Request,
    media_type: Literal["video", "image"] = Path(...),
) -> Any:
    query = parse_list_query(request, PUBLIC_MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        base_filter={"mediaType": media_type},
        map_doc=_map_public_media_doc,
    )


@router.get("/by-category/{category}")
@document_response(
    message="Media listed by category",
    summary="List public media by category slug (shortcut)",
    include_meta=True,
)
async def list_media_by_category(
    request: Request,
    category: CategorySlugEnum = Path(...),
) -> Any:
    reverse = {v: k for k, v in CATEGORY_PAIRS.items()}
    name_enum = reverse.get(category)
    if name_enum is None:
        raise HTTPException(404, detail=f"Unknown category slug: {category}")
    query = parse_list_query(request, PUBLIC_MEDIA_LIST_SPEC)
    return await run_list(
        collection=db.media,
        query=query,
        base_filter={"category": name_enum.value},
        map_doc=_map_public_media_doc,
    )


# ---------------------------------------------------------------------------
# Single item
# ---------------------------------------------------------------------------


@router.get("/{media_id}")
@document_response(
    message="Media retrieved",
    summary="Get public media by id",
)
async def get_media_by_id(media_id: str = Path(...)) -> MediaOut:
    item = await retrieve_media_by_id(media_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found"
        )
    return item


_ = ObjectId  # ObjectId is referenced by upstream repo helpers
