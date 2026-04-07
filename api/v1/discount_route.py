from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.discount_schema import (
    DiscountCreate,
    DiscountUpdate,
    DiscountOut,
    DiscountScope,
    DiscountStatus,
)
from services.discount_service import (
    add_discount,
    retrieve_discount_by_id,
    retrieve_discount_by_code,
    retrieve_discounts,
    update_discount_by_id,
    disable_discount,
    validate_discount_code,
    remove_discount,
)
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/discounts", tags=["Discounts"])


@router.post("")
@document_response(
    message="Discount created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new discount code (legacy admin only)",
    summary="Create discount",
)
async def create_discount_endpoint(
    payload: DiscountCreate,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut:
    """Create a new discount code for tenants."""
    return await add_discount(payload)


@router.get("")
@document_response(
    message="Discounts retrieved successfully",
    description="List discounts with optional filters (legacy admin only)",
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
) -> list[DiscountOut]:
    """List all discounts. Admin can filter by scope, status, or tenant."""
    return await retrieve_discounts(
        scope_filter=scope,
        status_filter=status_filter,
        tenant_id=tenant_id,
        start=skip,
        stop=skip + limit,
    )


@router.get("/code/{code}")
@document_response(
    message="Discount retrieved successfully",
    description="Look up a discount by its code",
    summary="Get discount by code",
)
async def get_discount_by_code_endpoint(
    code: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut | None:
    """Look up a discount by its code."""
    return await retrieve_discount_by_code(code)


@router.get("/{discount_id}")
@document_response(
    message="Discount retrieved successfully",
    description="Get a specific discount by ID",
    summary="Get discount",
)
async def get_discount_endpoint(
    discount_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut | None:
    """Retrieve a specific discount by ID."""
    return await retrieve_discount_by_id(discount_id)


@router.put("/{discount_id}")
@document_response(
    message="Discount updated successfully",
    description="Update a discount (legacy admin only)",
    summary="Update discount",
)
async def update_discount_endpoint(
    discount_id: str,
    payload: DiscountUpdate,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut | None:
    """Update a discount's properties."""
    return await update_discount_by_id(discount_id, payload)


@router.post("/{discount_id}/disable")
@document_response(
    message="Discount disabled successfully",
    description="Disable a discount code (legacy admin only)",
    summary="Disable discount",
)
async def disable_discount_endpoint(
    discount_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut | None:
    """Disable a discount code. It can no longer be redeemed."""
    return await disable_discount(discount_id)


@router.post("/validate")
@document_response(
    message="Discount code is valid",
    description="Validate a discount code for a specific tenant and plan",
    summary="Validate discount code",
)
async def validate_discount_endpoint(
    code: str = Query(...),
    tenant_id: str = Query(...),
    plan_id: str = Query(...),
    subscription_value: float = Query(0.0),
    admin=Depends(check_admin_account_status_and_permissions),
) -> DiscountOut:
    """Validate a discount code before applying it to a subscription."""
    return await validate_discount_code(code, tenant_id, plan_id, subscription_value)


@router.delete("/{discount_id}")
@document_response(
    message="Discount deleted successfully",
    description="Permanently delete a disabled discount with 0 redemptions (legacy admin only)",
    summary="Delete discount",
)
async def delete_discount_endpoint(
    discount_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> dict:
    """Permanently delete a discount. Only allowed for disabled discounts with 0 redemptions."""
    await remove_discount(discount_id)
    return {"deleted": True}
