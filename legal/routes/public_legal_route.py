"""Public-website legal document reads — ``/api/v1/legal/*``.

Unauthenticated. Only PUBLISHED documents are exposed, and only their live
``published_body`` (never drafts or the internal storage object key). The
default list page is served from the ``legal_documents.list_published``
precompute cache (GLOBAL scope).

Mirrors ``blog/routes/public_articles_route.py``.
"""

from __future__ import annotations

from typing import Any, List

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Path, Request, status

from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from legal.schemas.imports import LegalDocStatus, LegalDocType
from legal.schemas.legal_document_schema import (
    LegalDocumentPublicListRow,
    LegalDocumentPublicOut,
)
from legal.services.legal_document_service import retrieve_legal_document_by_slug

router = APIRouter(prefix="/legal", tags=["Public Legal"])

PUBLISHED_FILTER = {"status": LegalDocStatus.published.value}
_PRECOMPUTE_TTL = 300

_DOC_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in LegalDocType)


PUBLIC_LEGAL_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "published_at", "effective_at", "title"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("title", "slug", "summary"),
    filters={
        "docType": FilterDef(
            name="docType",
            mongo_field="doc_type",
            multi=True,
            allowed_values=_DOC_TYPE_VALUES,
        ),
    },
    facet_fields=frozenset({"doc_type"}),
    range_filters={"publishedAt": "published_at"},
)


def _public_row(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return LegalDocumentPublicListRow(**doc).model_dump(mode="json", by_alias=True)


def _is_default_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(PUBLIC_LEGAL_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(
        PUBLIC_LEGAL_LIST_SPEC.default_limit
    )


@router.get("")
@document_response(
    message="Published legal documents retrieved",
    description=(
        "Paginated published legal documents (compact rows, no body). Supports "
        "``skip``/``limit``, ``sort``, ``q``, ``docType`` filter, and ``facets``. "
        "``status == published`` is enforced server-side. The default first page "
        "is served from the precompute cache."
    ),
    summary="List published legal documents",
    include_meta=True,
)
async def list_published_legal_documents(request: Request) -> Any:
    if _is_default_listing(request):

        async def _loader():
            cursor = (
                db.legal_documents.find(PUBLISHED_FILTER)
                .sort([("date_created", -1)])
                .skip(0)
                .limit(PUBLIC_LEGAL_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_public_row(doc))
            total = await db.legal_documents.count_documents(PUBLISHED_FILTER)
            return {"items": items, "total": total}

        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="legal_documents.list_published",
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
                "limit": PUBLIC_LEGAL_LIST_SPEC.default_limit,
                "hasMore": total > PUBLIC_LEGAL_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, PUBLIC_LEGAL_LIST_SPEC)
    return await run_list(
        collection=db.legal_documents,
        query=query,
        base_filter=PUBLISHED_FILTER,
        map_doc=_public_row,
    )


@router.get("/{slug}")
@document_response(
    message="Published legal document retrieved",
    description=(
        "Fetch a published legal document by slug, returning the live "
        "``published_body``. 404 if the slug doesn't exist or isn't published."
    ),
    summary="Get a published legal document by slug",
)
async def get_published_legal_document(
    slug: str = Path(..., description="Legal document slug, e.g. 'privacy-policy'"),
) -> LegalDocumentPublicOut:
    doc = await retrieve_legal_document_by_slug(slug)
    if doc is None or doc.status != LegalDocStatus.published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal document not found or not published.",
        )
    return LegalDocumentPublicOut(**doc.model_dump(by_alias=False))
