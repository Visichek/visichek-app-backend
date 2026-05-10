from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Query, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.discount_schema import (
    DiscountCreate,
    DiscountUpdate,
)
from services.discount_service import (
    retrieve_discount_by_id,
    retrieve_discount_by_code,
    retrieve_discounts,
    validate_discount_code,
)
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/discounts", tags=["Discounts"])


DISCOUNTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "expires_at", "current_redemptions", "code"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("code", "description"),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=frozenset({"active", "expired", "disabled", "all"}),
            builder=lambda vs: (
                {} if "all" in vs else {"status": {"$in": list(vs)} if len(vs) > 1 else vs[0]}
            ),
        ),
        "discountType": FilterDef(
            name="discountType",
            mongo_field="discount_type",
            allowed_values=frozenset({"percentage", "fixed"}),
        ),
        "scope": FilterDef(
            name="scope",
            allowed_values=frozenset({"global", "tenant", "plan"}),
        ),
    },
    range_filters={"expiresAt": "valid_until"},
    facet_fields=frozenset({"status"}),
)


def _is_default_discount_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(DISCOUNTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(DISCOUNTS_LIST_SPEC.default_limit)


def _map_discount_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _discount_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in ("active", "disabled", "expired"):
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


@router.post("")
@document_response(
    message="Discount creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a discount-code create (application admin only).",
    summary="Create discount (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
)
async def create_discount_endpoint(
    payload: DiscountCreate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="discount.create",
        payload=payload.model_dump(exclude_none=True),
        resource_type="discount",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Discounts retrieved successfully",
    description=(
        "Paginated discounts list with filters / sort / q / facets. "
        "Unfiltered first page served from the global precompute cache."
    ),
    summary="List discounts",
    include_meta=True,
)
async def list_discounts_endpoint(
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_discount_listing(request):
        cached: List[Any] = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="discounts.list",
            ttl=60,
            loader=_load_discounts,
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: DISCOUNTS_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": DISCOUNTS_LIST_SPEC.default_limit,
                "hasMore": len(items) > DISCOUNTS_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, DISCOUNTS_LIST_SPEC)
    return await run_list(
        collection=db.discounts,
        query=query,
        map_doc=_map_discount_doc,
        facet_runner=_discount_status_facet,
    )


async def _load_discounts() -> List[Any]:
    discounts = await retrieve_discounts(start=0, stop=100)
    return [
        d.model_dump(mode="json", by_alias=True) if hasattr(d, "model_dump") else d
        for d in discounts
    ]


@router.get("/code/{code}")
@document_response(
    message="Discount retrieved successfully",
    summary="Get discount by code",
)
async def get_discount_by_code_endpoint(
    code: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    return await get_or_compute_entity(
        entity_type="discount_code",
        entity_id=code,
        loader=lambda: retrieve_discount_by_code(code),
    )


@router.get("/{discount_id}")
@document_response(
    message="Discount retrieved successfully",
    summary="Get discount",
)
async def get_discount_endpoint(
    discount_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    return await get_or_compute_entity(
        entity_type="discount",
        entity_id=discount_id,
        loader=lambda: retrieve_discount_by_id(discount_id),
    )


@router.put("/{discount_id}")
@document_response(
    message="Discount update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a discount update.",
    summary="Update discount (async)",
)
async def update_discount_endpoint(
    discount_id: str,
    payload: DiscountUpdate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="discount.update",
        payload=payload.model_dump(exclude_none=True),
        resource_type="discount",
        resource_id=discount_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/validate")
@document_response(
    message="Discount code is valid",
    description="Validate a discount code for a specific tenant and plan — stays sync for immediate feedback.",
    summary="Validate discount code",
)
async def validate_discount_endpoint(
    code: str = Query(...),
    tenant_id: str = Query(...),
    plan_id: str = Query(...),
    subscription_value: float = Query(0.0),
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    return await validate_discount_code(code, tenant_id, plan_id, subscription_value)


@router.post("/bulk/disable")
@document_response(
    message="Bulk discount disable queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk disable discounts",
)
async def bulk_disable_discounts(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/discounts/bulk/disable",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="discount.bulk_disable",
        ids=payload.get("ids", []),
        resource_type="discount",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/discounts/bulk/disable",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/delete")
@document_response(
    message="Bulk discount delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete discounts",
)
async def bulk_delete_discounts(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/discounts/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="discount.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="discount",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/discounts/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/{discount_id}/disable")
@document_response(
    message="Discount disable queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue disabling a discount code.",
    summary="Disable discount (async)",
)
async def disable_discount_endpoint(
    discount_id: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="discount.disable",
        payload={},
        resource_type="discount",
        resource_id=discount_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{discount_id}")
@document_response(
    message="Discount deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue hard delete for a disabled discount with 0 redemptions.",
    summary="Delete discount (async)",
)
async def delete_discount_endpoint(
    discount_id: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="discount.delete",
        payload={},
        resource_type="discount",
        resource_id=discount_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
