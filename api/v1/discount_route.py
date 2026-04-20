from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.discount_schema import (
    DiscountCreate,
    DiscountUpdate,
    DiscountOut,
    DiscountScope,
    DiscountStatus,
)
from services.discount_service import (
    retrieve_discount_by_id,
    retrieve_discount_by_code,
    retrieve_discounts,
    validate_discount_code,
)
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/discounts", tags=["Discounts"])


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
    description="Unfiltered first page served from the global precompute cache.",
    summary="List discounts",
    include_meta=True,
)
async def list_discounts_endpoint(
    scope: Optional[DiscountScope] = Query(None),
    status_filter: Optional[DiscountStatus] = Query(None, alias="status"),
    tenant_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    unfiltered = not (scope or status_filter or tenant_id)
    if unfiltered and skip == 0 and limit in (50, 100):
        cached: List[Any] = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="discounts.list",
            ttl=60,
            loader=_load_discounts,
        )
        return cached[:limit]
    return await retrieve_discounts(
        scope_filter=scope,
        status_filter=status_filter,
        tenant_id=tenant_id,
        start=skip,
        stop=skip + limit,
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
) -> DiscountOut | None:
    return await retrieve_discount_by_code(code)


@router.get("/{discount_id}")
@document_response(
    message="Discount retrieved successfully",
    summary="Get discount",
)
async def get_discount_endpoint(
    discount_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut | None:
    return await retrieve_discount_by_id(discount_id)


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
) -> DiscountOut:
    return await validate_discount_code(code, tenant_id, plan_id, subscription_value)


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
