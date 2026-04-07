from __future__ import annotations

import time
from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.subscription_repo import (
    create_subscription,
    get_subscription,
    get_subscriptions,
    update_subscription,
    delete_subscription,
)
from repositories.plan_repo import get_plan
from repositories.discount_repo import get_discount, increment_redemptions
from schemas.subscription_schema import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionOut,
    SubscriptionStatus,
    BillingCycle,
)
from schemas.plan_schema import PlanOut, PlanStatus
from schemas.discount_schema import DiscountOut, DiscountScope, DiscountStatus, DiscountType


def _calculate_period_end(start: int, cycle: BillingCycle) -> int:
    """Calculate period end timestamp from start + billing cycle."""
    if cycle == BillingCycle.MONTHLY:
        return start + (30 * 24 * 60 * 60)  # ~30 days
    else:
        return start + (365 * 24 * 60 * 60)  # ~365 days


def _calculate_effective_price(
    plan: PlanOut,
    billing_cycle: BillingCycle,
    discounts: List[DiscountOut],
) -> float:
    """Calculate final price after applying all valid discounts."""
    base = plan.base_price_monthly if billing_cycle == BillingCycle.MONTHLY else plan.base_price_yearly
    total_percentage_off = 0.0
    total_fixed_off = 0.0

    for discount in discounts:
        if discount.discount_type == DiscountType.PERCENTAGE:
            total_percentage_off += discount.value
        elif discount.discount_type == DiscountType.FIXED:
            total_fixed_off += discount.value

    # Cap percentage at 100
    total_percentage_off = min(total_percentage_off, 100.0)

    # Apply percentage first, then fixed
    price = base * (1 - total_percentage_off / 100.0)
    price = max(price - total_fixed_off, 0.0)
    return round(price, 2)


async def _validate_and_collect_discounts(
    discount_ids: List[str],
    tenant_id: str,
    plan: PlanOut,
    effective_price: float,
) -> List[DiscountOut]:
    """Validate discount codes and return applicable ones."""
    valid_discounts: List[DiscountOut] = []
    now = int(time.time())

    for did in discount_ids:
        if not ObjectId.is_valid(did):
            continue
        discount = await get_discount({"_id": ObjectId(did)})
        if not discount:
            continue
        if discount.status != DiscountStatus.ACTIVE:
            continue

        # Check validity window
        if discount.valid_from and now < discount.valid_from:
            continue
        if discount.valid_until and now > discount.valid_until:
            continue

        # Check max redemptions
        if discount.max_redemptions and discount.current_redemptions >= discount.max_redemptions:
            continue

        # Check scope
        if discount.scope == DiscountScope.TENANT and discount.target_tenant_id != tenant_id:
            continue
        if discount.scope == DiscountScope.PLAN and plan.id not in discount.target_plan_ids:
            continue

        # Check minimum subscription value
        if discount.min_subscription_value and effective_price < discount.min_subscription_value:
            continue

        # Check stackability (first discount is always allowed)
        if valid_discounts and not discount.stackable:
            continue
        if valid_discounts and not all(d.stackable for d in valid_discounts):
            continue

        valid_discounts.append(discount)

    return valid_discounts


async def subscribe_tenant(
    tenant_id: str,
    plan_id: str,
    billing_cycle: BillingCycle = BillingCycle.MONTHLY,
    discount_ids: Optional[List[str]] = None,
    trial_days: int = 0,
    admin_notes: Optional[str] = None,
    feature_overrides: Optional[dict] = None,
    crud_limit_overrides: Optional[dict] = None,
    retrieval_quota_overrides: Optional[dict] = None,
    tenant_cap_overrides: Optional[dict] = None,
) -> SubscriptionOut:
    """Create or update a tenant's subscription to a plan."""
    # Validate plan exists and is active
    if not ObjectId.is_valid(plan_id):
        raise HTTPException(status_code=400, detail="Invalid plan_id")
    plan = await get_plan({"_id": ObjectId(plan_id)})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != PlanStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Plan is not active")

    # Check if tenant already has an active subscription
    existing = await get_subscription({
        "tenant_id": tenant_id,
        "status": {"$in": [
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]},
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant already has an active subscription. Use change_plan or cancel first.",
        )

    # Calculate pricing with discounts
    now = int(time.time())
    base_price = plan.base_price_monthly if billing_cycle == BillingCycle.MONTHLY else plan.base_price_yearly

    valid_discounts: List[DiscountOut] = []
    if discount_ids:
        valid_discounts = await _validate_and_collect_discounts(
            discount_ids, tenant_id, plan, base_price,
        )

    effective_price = _calculate_effective_price(plan, billing_cycle, valid_discounts)

    # Increment redemption counters for used discounts
    applied_ids = []
    for d in valid_discounts:
        await increment_redemptions({"_id": ObjectId(d.id)})
        applied_ids.append(d.id)

    sub_status = SubscriptionStatus.TRIALING if trial_days > 0 else SubscriptionStatus.ACTIVE
    trial_ends_at = (now + trial_days * 86400) if trial_days > 0 else None
    period_end = _calculate_period_end(now, billing_cycle)

    sub_data = SubscriptionCreate(
        tenant_id=tenant_id,
        plan_id=plan_id,
        status=sub_status,
        billing_cycle=billing_cycle,
        effective_price=effective_price,
        currency=plan.currency,
        trial_ends_at=trial_ends_at,
        current_period_start=now,
        current_period_end=period_end,
        applied_discount_ids=applied_ids,
        admin_notes=admin_notes,
        feature_overrides=feature_overrides,
        crud_limit_overrides=crud_limit_overrides,
        retrieval_quota_overrides=retrieval_quota_overrides,
        tenant_cap_overrides=tenant_cap_overrides,
    )
    sub = await create_subscription(sub_data)

    # Invalidate cached plan data for this tenant
    from services.plan_cache_service import invalidate_tenant_plan_cache
    await invalidate_tenant_plan_cache(tenant_id)

    return sub


async def retrieve_subscription_by_id(sub_id: str) -> Optional[SubscriptionOut]:
    if not ObjectId.is_valid(sub_id):
        return None
    return await get_subscription({"_id": ObjectId(sub_id)})


async def retrieve_tenant_active_subscription(tenant_id: str) -> Optional[SubscriptionOut]:
    """Get the tenant's current active/trialing subscription."""
    return await get_subscription({
        "tenant_id": tenant_id,
        "status": {"$in": [
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]},
    })


async def retrieve_subscriptions(
    tenant_id: Optional[str] = None,
    status_filter: Optional[SubscriptionStatus] = None,
    start: int = 0,
    stop: int = 100,
) -> List[SubscriptionOut]:
    filter_dict = {}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    if status_filter:
        filter_dict["status"] = status_filter.value
    return await get_subscriptions(filter_dict, start=start, stop=stop)


async def change_plan(
    tenant_id: str,
    new_plan_id: str,
    billing_cycle: Optional[BillingCycle] = None,
) -> SubscriptionOut:
    """Immediately switch a tenant to a different plan. Takes effect now."""
    current = await retrieve_tenant_active_subscription(tenant_id)
    if not current:
        raise HTTPException(status_code=404, detail="No active subscription found for tenant")

    if not ObjectId.is_valid(new_plan_id):
        raise HTTPException(status_code=400, detail="Invalid new_plan_id")
    new_plan = await get_plan({"_id": ObjectId(new_plan_id)})
    if not new_plan:
        raise HTTPException(status_code=404, detail="New plan not found")
    if new_plan.status != PlanStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="New plan is not active")

    cycle = billing_cycle or current.billing_cycle
    now = int(time.time())

    # Recalculate price (carry forward existing discounts)
    valid_discounts = []
    for did in current.applied_discount_ids:
        if ObjectId.is_valid(did):
            d = await get_discount({"_id": ObjectId(did)})
            if d and d.status == DiscountStatus.ACTIVE:
                valid_discounts.append(d)

    effective_price = _calculate_effective_price(new_plan, cycle, valid_discounts)

    update_data = SubscriptionUpdate(
        plan_id=new_plan_id,
        billing_cycle=cycle,
        effective_price=effective_price,
        currency=new_plan.currency,
        current_period_start=now,
        current_period_end=_calculate_period_end(now, cycle),
    )
    updated = await update_subscription(
        {"_id": ObjectId(current.id)},
        update_data,
    )

    # Invalidate cache
    from services.plan_cache_service import invalidate_tenant_plan_cache
    await invalidate_tenant_plan_cache(tenant_id)

    return updated


async def cancel_subscription(
    tenant_id: str,
    reason: Optional[str] = None,
    immediate: bool = False,
) -> SubscriptionOut:
    """Cancel a tenant's subscription."""
    current = await retrieve_tenant_active_subscription(tenant_id)
    if not current:
        raise HTTPException(status_code=404, detail="No active subscription to cancel")

    now = int(time.time())
    new_status = SubscriptionStatus.CANCELLED if immediate else SubscriptionStatus.ACTIVE

    update_data = SubscriptionUpdate(
        status=new_status,
        cancelled_at=now,
        cancellation_reason=reason,
    )
    if immediate:
        update_data.current_period_end = now

    updated = await update_subscription(
        {"_id": ObjectId(current.id)},
        update_data,
    )

    from services.plan_cache_service import invalidate_tenant_plan_cache
    await invalidate_tenant_plan_cache(tenant_id)

    return updated


async def update_subscription_overrides(
    sub_id: str,
    feature_overrides: Optional[dict] = None,
    crud_limit_overrides: Optional[dict] = None,
    retrieval_quota_overrides: Optional[dict] = None,
    tenant_cap_overrides: Optional[dict] = None,
) -> Optional[SubscriptionOut]:
    """Update tenant-specific overrides on a subscription."""
    if not ObjectId.is_valid(sub_id):
        return None
    sub = await get_subscription({"_id": ObjectId(sub_id)})
    if not sub:
        return None

    update_data = SubscriptionUpdate()
    if feature_overrides is not None:
        update_data.feature_overrides = feature_overrides
    if crud_limit_overrides is not None:
        update_data.crud_limit_overrides = crud_limit_overrides
    if retrieval_quota_overrides is not None:
        update_data.retrieval_quota_overrides = retrieval_quota_overrides
    if tenant_cap_overrides is not None:
        update_data.tenant_cap_overrides = tenant_cap_overrides

    updated = await update_subscription({"_id": ObjectId(sub_id)}, update_data)

    from services.plan_cache_service import invalidate_tenant_plan_cache
    await invalidate_tenant_plan_cache(sub.tenant_id)

    return updated
