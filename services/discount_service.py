from __future__ import annotations

import time
from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.discount_repo import (
    create_discount,
    get_discount,
    get_discounts,
    update_discount,
    delete_discount,
)
from schemas.discount_schema import (
    DiscountCreate,
    DiscountUpdate,
    DiscountOut,
    DiscountScope,
    DiscountStatus,
)


async def add_discount(
    discount_data: DiscountCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> DiscountOut:
    """Create a new discount code."""
    # Check for duplicate code
    existing = await get_discount({"code": discount_data.code})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Discount code '{discount_data.code}' already exists",
        )
    return await create_discount(discount_data, preassigned_id=preassigned_id)


async def retrieve_discount_by_id(discount_id: str) -> Optional[DiscountOut]:
    if not ObjectId.is_valid(discount_id):
        return None
    return await get_discount({"_id": ObjectId(discount_id)})


async def retrieve_discount_by_code(code: str) -> Optional[DiscountOut]:
    return await get_discount({"code": code.upper()})


async def retrieve_discounts(
    scope_filter: Optional[DiscountScope] = None,
    status_filter: Optional[DiscountStatus] = None,
    tenant_id: Optional[str] = None,
    start: int = 0,
    stop: int = 100,
) -> List[DiscountOut]:
    """List discounts with optional filters."""
    filter_dict = {}
    if scope_filter:
        filter_dict["scope"] = scope_filter.value
    if status_filter:
        filter_dict["status"] = status_filter.value
    if tenant_id:
        filter_dict["target_tenant_id"] = tenant_id
    return await get_discounts(filter_dict, start=start, stop=stop)


async def update_discount_by_id(
    discount_id: str, data: DiscountUpdate
) -> Optional[DiscountOut]:
    if not ObjectId.is_valid(discount_id):
        return None
    return await update_discount({"_id": ObjectId(discount_id)}, data)


async def disable_discount(discount_id: str) -> Optional[DiscountOut]:
    return await update_discount_by_id(
        discount_id,
        DiscountUpdate(status=DiscountStatus.DISABLED),
    )


async def validate_discount_code(
    code: str,
    tenant_id: str,
    plan_id: str,
    subscription_value: float,
) -> DiscountOut:
    """Validate a discount code for a specific tenant and plan.
    Returns the discount if valid, raises HTTPException if not.
    """
    discount = await retrieve_discount_by_code(code)
    if not discount:
        raise HTTPException(status_code=404, detail="Discount code not found")

    now = int(time.time())

    if discount.status != DiscountStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Discount code is not active")

    if discount.valid_from and now < discount.valid_from:
        raise HTTPException(status_code=400, detail="Discount code is not yet valid")

    if discount.valid_until and now > discount.valid_until:
        raise HTTPException(status_code=400, detail="Discount code has expired")

    if (
        discount.max_redemptions
        and discount.current_redemptions >= discount.max_redemptions
    ):
        raise HTTPException(
            status_code=400, detail="Discount code has reached max redemptions"
        )

    if (
        discount.scope == DiscountScope.TENANT
        and discount.target_tenant_id != tenant_id
    ):
        raise HTTPException(
            status_code=400, detail="Discount code is not valid for this tenant"
        )

    if discount.scope == DiscountScope.PLAN and plan_id not in discount.target_plan_ids:
        raise HTTPException(
            status_code=400, detail="Discount code is not valid for this plan"
        )

    if (
        discount.min_subscription_value
        and subscription_value < discount.min_subscription_value
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Subscription value must be at least {discount.min_subscription_value}",
        )

    return discount


async def remove_discount(discount_id: str) -> bool:
    """Hard delete a discount. Only allowed for disabled discounts with 0 redemptions."""
    discount = await retrieve_discount_by_id(discount_id)
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")
    if discount.status == DiscountStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete an active discount. Disable it first.",
        )
    if discount.current_redemptions > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a discount that has been redeemed. Disable it instead.",
        )
    await delete_discount({"_id": ObjectId(discount_id)})
    return True


async def expire_stale_discounts() -> int:
    """Background task: mark expired discounts whose valid_until has passed."""
    now = int(time.time())
    discounts = await get_discounts(
        {
            "status": DiscountStatus.ACTIVE.value,
            "valid_until": {"$lt": now, "$ne": None},
        }
    )
    count = 0
    for d in discounts:
        await update_discount(
            {"_id": ObjectId(d.id)},
            DiscountUpdate(status=DiscountStatus.EXPIRED),
        )
        count += 1
    return count
