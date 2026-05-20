"""Admin blog routes — ``/v1/blogs/*``.

Listing style follows the host backend's tables contract:
``ListSpec`` + ``parse_list_query`` + ``run_list`` from
``core.list_params`` / ``core.list_runner``. Sortable fields, search
fields, filters, and facets are allowlisted on ``BLOGS_LIST_SPEC`` so
clients can't probe arbitrary Mongo paths.

Mutations route through the queued-write pipeline and return
``202 + job_id``. The unfiltered first page of the admin list is
served from the precompute cache (``blogs.list_admin``, global scope).
"""

from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from blog.schemas.blog_schema import BlogBase, BlogCreate, BlogOut, BlogUpdate
from blog.services.blog_service import retrieve_blog_by_blog_id
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/blogs", tags=["Blog Admin"])

_PRECOMPUTE_TTL = 300


# ---------------------------------------------------------------------------
# ListSpec — single source of truth for sortable/searchable/filterable fields
# ---------------------------------------------------------------------------


BLOGS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {
            "date_created",
            "last_updated",
            "publishDate",
            "title",
            "state",
            "blogType",
        }
    ),
    default_sort=(("date_created", -1),),
    search_fields=("title", "excerpt", "author.name"),
    filters={
        "state": FilterDef(
            name="state",
            multi=True,
            allowed_values=frozenset({"draft", "published"}),
        ),
        "blogType": FilterDef(
            name="blogType",
            mongo_field="blogType",
            multi=True,
            allowed_values=frozenset(
                {"editors pick", "featured story", "hero section", "normal"}
            ),
        ),
        "category": FilterDef(
            name="category",
            mongo_field="category.slug",
            multi=True,
        ),
        "author": FilterDef(
            name="author",
            mongo_field="author.name",
        ),
    },
    facet_fields=frozenset({"state", "blogType"}),
    range_filters={"createdAt": "date_created", "updatedAt": "last_updated"},
)


def _map_blog_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


def _is_default_admin_blog_listing(request: Request) -> bool:
    """Default first-page admin query — eligible for the precompute cache."""
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(BLOGS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(BLOGS_LIST_SPEC.default_limit)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Blogs retrieved",
    description=(
        "Paginated admin blog list. Supports ``skip``/``limit``, "
        "``sort=field,-field``, ``q`` free-text search, allow-listed "
        "filters (``state``, ``blogType``, ``category``, ``author``), "
        "and ``facets=state,blogType``. The unfiltered first page is "
        "served from the precompute cache."
    ),
    summary="List blogs",
    include_meta=True,
)
async def list_blogs_endpoint(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_admin_blog_listing(request):

        async def _loader():
            cursor = (
                db.blogs.find({})
                .sort([("date_created", -1)])
                .skip(0)
                .limit(BLOGS_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_map_blog_doc(doc))
            total = await db.blogs.count_documents({})
            return {"items": items, "total": total}

        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="blogs.list_admin",
            ttl=_PRECOMPUTE_TTL,
            loader=_loader,
        )
        if isinstance(cached, dict):
            items = cached.get("items", []) or []
            total = cached.get("total", len(items))
        elif isinstance(cached, list):  # legacy payload from earlier cache hits
            items = cached
            total = len(items)
        else:
            items, total = [], 0
        return {
            "items": items,
            "meta": {
                "total": total,
                "skip": 0,
                "limit": BLOGS_LIST_SPEC.default_limit,
                "hasMore": total > BLOGS_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, BLOGS_LIST_SPEC)
    return await run_list(
        collection=db.blogs,
        query=query,
        map_doc=_map_blog_doc,
    )


# ---------------------------------------------------------------------------
# Shortcut: /recent — preserved for FE compat; delegates to list spec with
# date_created sort + larger default limit.
# ---------------------------------------------------------------------------


@router.get("/recent")
@document_response(
    message="Recent blogs retrieved",
    description=(
        "Shortcut: equivalent to ``GET /v1/blogs?sort=-date_created&limit=50``. "
        "Accepts the same query params as the main list endpoint."
    ),
    summary="List most-recent blogs (shortcut)",
    include_meta=True,
)
async def list_recent_blogs_endpoint(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    query = parse_list_query(request, BLOGS_LIST_SPEC)
    if request.query_params.get("limit") is None:
        query.limit = 50
    return await run_list(
        collection=db.blogs,
        query=query,
        map_doc=_map_blog_doc,
    )


# ---------------------------------------------------------------------------
# Single blog
# ---------------------------------------------------------------------------


@router.get("/{blog_id}")
@document_response(
    message="Blog retrieved",
    description="Retrieve a single blog by id (admin view, includes drafts).",
    summary="Get blog by id",
)
async def get_blog_by_id_endpoint(
    blog_id: str = Path(..., description="Blog id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> BlogOut:
    return await retrieve_blog_by_blog_id(id=blog_id)


# ---------------------------------------------------------------------------
# Mutations — queued
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Blog creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a blog creation. The response carries the pre-generated id "
        "and a `job_id` you can poll via `GET /v1/jobs/{job_id}`."
    ),
    summary="Create blog (queued)",
)
async def create_blog_endpoint(
    payload: BlogBase,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    full = BlogCreate(**payload.model_dump()).model_dump(mode="json")
    return await enqueue_write(
        writer_key="blog.create",
        payload=full,
        resource_type="blog",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{blog_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Blog update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Queue a partial update for a blog. Poll the returned job for status.",
    summary="Update blog (queued)",
)
async def update_blog_endpoint(
    payload: BlogUpdate,
    request: Request,
    blog_id: str = Path(..., description="Blog id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if payload.currentPageBody == []:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED, detail="No update made"
        )
    return await enqueue_write(
        writer_key="blog.update",
        payload=payload.model_dump(mode="json", exclude_none=True),
        resource_type="blog",
        resource_id=blog_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{blog_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Blog deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Queue a blog deletion.",
    summary="Delete blog (queued)",
)
async def delete_blog_endpoint(
    request: Request,
    blog_id: str = Path(..., description="Blog id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    return await enqueue_write(
        writer_key="blog.delete",
        payload={},
        resource_type="blog",
        resource_id=blog_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


_ = Optional  # imported above for completeness in docstring example
