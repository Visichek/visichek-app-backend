"""Public-website article reads — ``/api/v1/articles/content/*``.

Listing style follows the host backend's tables contract:
``ListSpec`` + ``parse_list_query`` + ``run_list``. All endpoints lock
``state == published`` into ``base_filter`` so the query string cannot
expose drafts.

The unfiltered default page is served from the precompute cache
(``blogs.list_published``, global scope).
"""

from __future__ import annotations

from typing import Any, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from blog.schemas.blog_schema import BlogOutUserVersion
from blog.schemas.imports import (
    BlogStatus,
    CATEGORY_PAIRS,
    Category,
    CategorySlugEnum,
    ListOfCategories,
    PublicBlogType,
    SearchQuery,
)
from blog.services.blog_search_service import search_blogs_service
from blog.services.blog_service import retrieve_blog_by_blog_id
from blog.services.blog_utils import get_category_image_url
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response

router = APIRouter(prefix="/articles/content", tags=["Public Blog"])

PUBLISHED_FILTER = {"state": BlogStatus.published.value}
_PRECOMPUTE_TTL = 300


# Map the public ``blog_type`` slug enum (hero-section, editors-pick, ...)
# to the underlying Mongo value (hero section, editors pick, ...). Used by
# the ``/by-blog-type/{}`` shortcut to translate the path param.
_BLOG_TYPE_MAP = {
    PublicBlogType.hero_section.value: "hero section",
    PublicBlogType.editors_pick.value: "editors pick",
    PublicBlogType.featured_story.value: "featured story",
    PublicBlogType.normal.value: "normal",
}


PUBLIC_BLOGS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "last_updated", "publishDate", "title", "blogType"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("title", "excerpt", "author.name"),
    filters={
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
    facet_fields=frozenset({"blogType", "category.slug"}),
    range_filters={"publishedAt": "publishDate"},
)


def _map_public_blog_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


def _is_default_public_blog_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(PUBLIC_BLOGS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(
        PUBLIC_BLOGS_LIST_SPEC.default_limit
    )


# ---------------------------------------------------------------------------
# Categories — small static list, no pagination
# ---------------------------------------------------------------------------


@router.get("/categories")
@document_response(
    message="Categories retrieved",
    description="List every available blog category with its slug and cover image.",
    summary="List categories",
    include_meta=True,
)
async def list_all_categories() -> ListOfCategories:
    total = len(CATEGORY_PAIRS)
    categories = [
        Category(
            imageUrl=get_category_image_url(slug.value),
            name=name,
            slug=slug,
            itemIndex=i,
        )
        for i, (name, slug) in enumerate(CATEGORY_PAIRS.items(), start=1)
    ]
    return ListOfCategories(listOfCategories=categories, totalItems=total)


# ---------------------------------------------------------------------------
# Main list
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Published blogs retrieved",
    description=(
        "Paginated published blogs. Supports ``skip``/``limit``, ``sort``, "
        "``q`` (title/author/excerpt), filters ``blogType``, ``category``, "
        "``author``, and ``facets``. ``state == published`` is enforced "
        "server-side and cannot be overridden by the query string. "
        "The default first page is served from the precompute cache."
    ),
    summary="List published blogs",
    include_meta=True,
)
async def list_published_blogs(
    request: Request,
) -> Any:
    if _is_default_public_blog_listing(request):

        async def _loader():
            cursor = (
                db.blogs.find(PUBLISHED_FILTER)
                .sort([("date_created", -1)])
                .skip(0)
                .limit(PUBLIC_BLOGS_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_map_public_blog_doc(doc))
            total = await db.blogs.count_documents(PUBLISHED_FILTER)
            return {"items": items, "total": total}

        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="blogs.list_published",
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
                "limit": PUBLIC_BLOGS_LIST_SPEC.default_limit,
                "hasMore": total > PUBLIC_BLOGS_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, PUBLIC_BLOGS_LIST_SPEC)
    return await run_list(
        collection=db.blogs,
        query=query,
        base_filter=PUBLISHED_FILTER,
        map_doc=_map_public_blog_doc,
    )


# ---------------------------------------------------------------------------
# Shortcuts — path-style filters (preserved from blog backend's URLs)
# ---------------------------------------------------------------------------


@router.get("/by-blog-type/{blog_type}")
@document_response(
    message="Published blogs by type retrieved",
    description=(
        "Shortcut: locks ``blogType`` to the path value. Accepts the same "
        "query params as the main list endpoint."
    ),
    summary="List published blogs by blog type (shortcut)",
    include_meta=True,
)
async def list_blogs_by_blog_type(
    request: Request,
    blog_type: PublicBlogType = Path(...),
) -> Any:
    blog_type_value = _BLOG_TYPE_MAP.get(blog_type.value)
    if blog_type_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported blog_type: {blog_type.value!r}",
        )
    query = parse_list_query(request, PUBLIC_BLOGS_LIST_SPEC)
    return await run_list(
        collection=db.blogs,
        query=query,
        base_filter={**PUBLISHED_FILTER, "blogType": blog_type_value},
        map_doc=_map_public_blog_doc,
    )


@router.get("/by-category-slug/{slug}")
@document_response(
    message="Published blogs by category retrieved",
    description=(
        "Shortcut: locks ``category.slug`` to the path value. Accepts the "
        "same query params as the main list endpoint."
    ),
    summary="List published blogs by category slug (shortcut)",
    include_meta=True,
)
async def list_blogs_by_category_slug(
    request: Request,
    slug: CategorySlugEnum = Path(...),
) -> Any:
    query = parse_list_query(request, PUBLIC_BLOGS_LIST_SPEC)
    return await run_list(
        collection=db.blogs,
        query=query,
        base_filter={**PUBLISHED_FILTER, "category.slug": slug.value},
        map_doc=_map_public_blog_doc,
    )


@router.get("/by-author-name")
@document_response(
    message="Published blogs by author retrieved",
    description=(
        "Shortcut: locks ``author.name`` to the supplied query param. "
        "Accepts the same query params as the main list endpoint, plus "
        "``author_name`` which is required."
    ),
    summary="List published blogs by author (shortcut)",
    include_meta=True,
)
async def list_blogs_by_author_name(
    request: Request,
    author_name: str,
) -> Any:
    query = parse_list_query(request, PUBLIC_BLOGS_LIST_SPEC)
    return await run_list(
        collection=db.blogs,
        query=query,
        base_filter={**PUBLISHED_FILTER, "author.name": author_name},
        map_doc=_map_public_blog_doc,
    )


# ---------------------------------------------------------------------------
# Single detail + search
# ---------------------------------------------------------------------------


@router.get("/{blog_id}")
@document_response(
    message="Published blog retrieved",
    summary="Get a published blog by id",
)
async def get_published_blog_by_id(
    blog_id: str = Path(..., description="Blog id"),
) -> BlogOutUserVersion:
    item = await retrieve_blog_by_blog_id(id=blog_id)
    if item.state != BlogStatus.published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blog not found or is not published",
        )
    return BlogOutUserVersion(**item.model_dump())


@router.get("/search/")
@document_response(
    message="Search results retrieved",
    description=(
        "Search published articles by title or author (case-insensitive "
        "regex). Preserved for backward compat; the main ``GET /`` endpoint "
        "supports the same search via ``?q=``."
    ),
    summary="Search published blogs (legacy)",
    include_meta=True,
)
async def search_published_blogs_legacy(
    query_params: SearchQuery = Depends(),
):
    if (not query_params.title or not query_params.title.strip()) and (
        not query_params.author or not query_params.author.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query 'title' and 'author' parameter cannot be empty.",
        )
    return await search_blogs_service(query_params)
