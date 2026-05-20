from __future__ import annotations

import time
from typing import Any, List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from core.queue.precompute import delete_precompute
from repositories.subscription_repo import (
    create_subscription,
    get_subscription,
    get_subscriptions,
    update_subscription,
)
from repositories.plan_repo import get_plan
from repositories.discount_repo import get_discount, increment_redemptions
from schemas.subscription_schema import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionOut,
    SubscriptionStatus,
    SubscriptionWithDetailsOut,
    SubscriptionTenantInfo,
    SubscriptionPlanInfo,
    BillingCycle,
)
from schemas.plan_schema import PlanOut, PlanStatus
from schemas.discount_schema import (
    DiscountOut,
    DiscountScope,
    DiscountStatus,
    DiscountType,
)
from schemas.summary_schema import (
    DiscountBriefSummary,
    PlanBriefSummary,
    TenantBriefSummary,
)
from services.audit_service import record_audit_event


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
    base = (
        plan.base_price_monthly
        if billing_cycle == BillingCycle.MONTHLY
        else plan.base_price_yearly
    )
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
        if (
            discount.max_redemptions
            and discount.current_redemptions >= discount.max_redemptions
        ):
            continue

        # Check scope
        if (
            discount.scope == DiscountScope.TENANT
            and discount.target_tenant_id != tenant_id
        ):
            continue
        if (
            discount.scope == DiscountScope.PLAN
            and plan.id not in discount.target_plan_ids
        ):
            continue

        # Check minimum subscription value
        if (
            discount.min_subscription_value
            and effective_price < discount.min_subscription_value
        ):
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

    # Check if tenant already has an active subscription. The free plan
    # is the platform default — we auto-expire it here so the tenant can
    # cleanly upgrade to a paid plan without first calling /cancel.
    existing = await get_subscription(
        {
            "tenant_id": tenant_id,
            "status": {
                "$in": [
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.TRIALING.value,
                ]
            },
        }
    )
    if existing:
        from config.plan_tiers import FREE_PLAN_NAME

        existing_plan = (
            await get_plan({"_id": ObjectId(existing.plan_id)})
            if (existing.plan_id and ObjectId.is_valid(existing.plan_id))
            else None
        )
        is_free_plan = bool(existing_plan and existing_plan.name == FREE_PLAN_NAME)

        if not is_free_plan:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Tenant already has an active subscription. "
                    "Use change_plan or cancel first."
                ),
            )

        # Free → paid upgrade: expire the free sub so the new paid one
        # becomes the single active subscription.
        if existing.id:
            await update_subscription(
                {"_id": ObjectId(existing.id)},
                SubscriptionUpdate(
                    status=SubscriptionStatus.EXPIRED,
                    cancelled_at=int(time.time()),
                    cancellation_reason="Upgraded to paid plan",
                    current_period_end=int(time.time()),
                ),
            )

    # Calculate pricing with discounts
    now = int(time.time())
    base_price = (
        plan.base_price_monthly
        if billing_cycle == BillingCycle.MONTHLY
        else plan.base_price_yearly
    )

    valid_discounts: List[DiscountOut] = []
    if discount_ids:
        valid_discounts = await _validate_and_collect_discounts(
            discount_ids,
            tenant_id,
            plan,
            base_price,
        )

    effective_price = _calculate_effective_price(plan, billing_cycle, valid_discounts)

    # Increment redemption counters for used discounts
    applied_ids = []
    for d in valid_discounts:
        await increment_redemptions({"_id": ObjectId(d.id)})
        applied_ids.append(d.id)

    sub_status = (
        SubscriptionStatus.TRIALING if trial_days > 0 else SubscriptionStatus.ACTIVE
    )
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
        applied_discount_ids=[d for d in applied_ids if d],
        admin_notes=admin_notes,
        feature_overrides=feature_overrides,
        crud_limit_overrides=crud_limit_overrides,
        retrieval_quota_overrides=retrieval_quota_overrides,
        tenant_cap_overrides=tenant_cap_overrides,
    )
    sub = await create_subscription(sub_data)

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="subscription.created",
            resource_type="subscription",
            resource_id=str(sub.id),
            tenant_id=tenant_id,
            details={
                "plan_id": plan_id,
                "billing_cycle": billing_cycle.value,
                "effective_price": effective_price,
                "trial_days": trial_days,
                "applied_discount_ids": applied_ids,
            },
        )
    except Exception:
        pass  # Fire-and-forget: don't block subscription creation if audit fails

    # Invalidate cached plan data for this tenant
    from services.plan_cache_service import invalidate_tenant_plan_cache

    await invalidate_tenant_plan_cache(tenant_id)
    delete_precompute("dashboard.stats", tenant_id=tenant_id)

    return sub


def _build_tenant_summary(tenant) -> TenantBriefSummary:
    return TenantBriefSummary(
        id=tenant.id or "",
        company_name=tenant.company_name,
        is_active=tenant.is_active,
        country_of_hosting=tenant.country_of_hosting,
    )


def _build_plan_summary(plan) -> PlanBriefSummary:
    return PlanBriefSummary(
        id=plan.id or "",
        name=plan.name,
        display_name=plan.display_name,
        tier=plan.tier.value if hasattr(plan.tier, "value") else plan.tier,
    )


def _build_discount_summary(discount) -> DiscountBriefSummary:
    return DiscountBriefSummary(
        id=discount.id or "",
        code=discount.code,
        name=discount.name,
        discount_type=discount.discount_type.value
        if hasattr(discount.discount_type, "value")
        else discount.discount_type,
        value=discount.value,
        status=discount.status.value
        if hasattr(discount.status, "value")
        else discount.status,
    )


async def _enrich_subscription_summaries(
    sub: SubscriptionOut,
) -> SubscriptionOut:
    """Populate ``tenant_summary`` / ``plan_summary`` / ``applied_discount_summaries``
    on a single subscription. Best-effort — leaves a field unset on lookup failure.
    """
    import asyncio

    from services.summary_resolver import (
        resolve_discount_summary,
        resolve_plan_summary,
        resolve_tenant_summary,
    )

    tenant_s, plan_s = await asyncio.gather(
        resolve_tenant_summary(sub.tenant_id),
        resolve_plan_summary(sub.plan_id),
    )
    discount_results = await asyncio.gather(
        *[resolve_discount_summary(d) for d in sub.applied_discount_ids]
    )

    sub.tenant_summary = tenant_s
    sub.plan_summary = plan_s
    sub.applied_discount_summaries = [d for d in discount_results if d is not None]
    return sub


async def retrieve_subscription_by_id(sub_id: str) -> Optional[SubscriptionOut]:
    if not ObjectId.is_valid(sub_id):
        return None
    sub = await get_subscription({"_id": ObjectId(sub_id)})
    if sub is None:
        return None
    return await _enrich_subscription_summaries(sub)


async def retrieve_tenant_active_subscription(
    tenant_id: str,
) -> Optional[SubscriptionOut]:
    """Get the tenant's current active/trialing subscription."""
    sub = await get_subscription(
        {
            "tenant_id": tenant_id,
            "status": {
                "$in": [
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.TRIALING.value,
                ]
            },
        }
    )
    if sub is None:
        return None
    return await _enrich_subscription_summaries(sub)


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


def _build_tenant_info(t) -> SubscriptionTenantInfo:
    return SubscriptionTenantInfo(
        id=t.id,
        company_name=t.company_name,
        is_active=t.is_active,
        country_of_hosting=t.country_of_hosting,
        dpo_contact_email=t.dpo_contact_email,
        default_payment_provider=t.default_payment_provider,
        stripe_customer_id=t.stripe_customer_id,
        flutterwave_customer_id=t.flutterwave_customer_id,
    )


def _build_plan_info(p) -> SubscriptionPlanInfo:
    caps = p.tenant_caps.model_dump() if p.tenant_caps else None
    return SubscriptionPlanInfo(
        id=p.id,
        name=p.name,
        display_name=p.display_name,
        tier=p.tier,
        description=p.description,
        base_price_monthly=p.base_price_monthly,
        base_price_yearly=p.base_price_yearly,
        currency=p.currency,
        priority_support=p.priority_support,
        custom_branding=p.custom_branding,
        api_access=p.api_access,
        tenant_caps=caps,
    )


async def retrieve_subscriptions_with_details(
    tenant_id: Optional[str] = None,
    status_filter: Optional[SubscriptionStatus] = None,
    start: int = 0,
    stop: int = 100,
) -> List[SubscriptionWithDetailsOut]:
    """Bulk-enriched subscriptions list.

    Previously did 2 Mongo queries (tenant + plan) per subscription via
    ``asyncio.gather``; for a page of 50 that's 100 round trips which
    partially serialises on the event loop. Now: one ``find`` for the
    matching tenants, one for the matching plans, then a dict lookup per
    row. O(1) round-trip cost regardless of page size.
    """
    from core.database import db
    from bson import ObjectId as BsonObjectId

    subs = await retrieve_subscriptions(
        tenant_id=tenant_id, status_filter=status_filter, start=start, stop=stop
    )
    if not subs:
        return []

    tenant_oids: list[BsonObjectId] = []
    plan_oids: list[BsonObjectId] = []
    discount_oids: list[BsonObjectId] = []
    for s in subs:
        if s.tenant_id and BsonObjectId.is_valid(s.tenant_id):
            tenant_oids.append(BsonObjectId(s.tenant_id))
        if s.plan_id and BsonObjectId.is_valid(s.plan_id):
            plan_oids.append(BsonObjectId(s.plan_id))
        for did in s.applied_discount_ids or []:
            if did and BsonObjectId.is_valid(did):
                discount_oids.append(BsonObjectId(did))

    tenants_by_id: dict[str, Any] = {}
    plans_by_id: dict[str, Any] = {}
    discounts_by_id: dict[str, Any] = {}
    if tenant_oids:
        from schemas.tenant_schema import TenantOut

        async for doc in db["tenant_companies"].find({"_id": {"$in": tenant_oids}}):
            try:
                tenant_model = TenantOut(**doc)
            except Exception:
                continue
            if tenant_model.id:
                tenants_by_id[tenant_model.id] = tenant_model
    if plan_oids:
        from schemas.plan_schema import PlanOut

        async for doc in db["plans"].find({"_id": {"$in": plan_oids}}):
            try:
                plan_model = PlanOut(**doc)
            except Exception:
                continue
            if plan_model.id:
                plans_by_id[plan_model.id] = plan_model
    if discount_oids:
        async for doc in db["discounts"].find({"_id": {"$in": discount_oids}}):
            try:
                discount_model = DiscountOut(**doc)
            except Exception:
                continue
            if discount_model.id:
                discounts_by_id[discount_model.id] = discount_model

    results: List[SubscriptionWithDetailsOut] = []
    for sub in subs:
        data = sub.model_dump(by_alias=False)
        t = tenants_by_id.get(sub.tenant_id or "") if sub.tenant_id else None
        p = plans_by_id.get(sub.plan_id or "") if sub.plan_id else None
        data["tenant"] = _build_tenant_info(t) if t else None
        data["plan"] = _build_plan_info(p) if p else None
        data["tenant_summary"] = _build_tenant_summary(t) if t else None
        data["plan_summary"] = _build_plan_summary(p) if p else None
        data["applied_discount_summaries"] = [
            _build_discount_summary(discounts_by_id[d])
            for d in (sub.applied_discount_ids or [])
            if d in discounts_by_id
        ]
        results.append(SubscriptionWithDetailsOut(**data))
    return results


async def change_plan(
    tenant_id: str,
    new_plan_id: str,
    billing_cycle: Optional[BillingCycle] = None,
) -> Optional[SubscriptionOut]:
    """Immediately switch a tenant to a different plan. Takes effect now."""
    current = await retrieve_tenant_active_subscription(tenant_id)
    if not current:
        raise HTTPException(
            status_code=404, detail="No active subscription found for tenant"
        )

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

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="subscription.plan_changed",
            resource_type="subscription",
            resource_id=str(current.id),
            tenant_id=tenant_id,
            details={
                "old_plan_id": str(current.plan_id),
                "new_plan_id": new_plan_id,
                "billing_cycle": cycle.value,
                "effective_price": effective_price,
            },
        )
    except Exception:
        pass

    # Invalidate cache
    from services.plan_cache_service import invalidate_tenant_plan_cache

    await invalidate_tenant_plan_cache(tenant_id)
    delete_precompute("dashboard.stats", tenant_id=tenant_id)

    return updated


async def provision_plan_change_from_checkout(
    existing_sub_id: str,
    tenant_id: str,
    new_plan_id: str,
    billing_cycle: BillingCycle,
    discount_ids: Optional[List[str]] = None,
) -> Optional[SubscriptionOut]:
    """Switch a tenant onto a new plan after they paid via checkout.

    Differs from :func:`change_plan` by replacing (not carrying forward)
    the discount set, forcing the subscription to ``ACTIVE`` (so a paid
    checkout converts a trialing sub), clearing ``trial_ends_at``, and
    resetting renewal counters. Used by the checkout completion path
    when the tenant already has an active/trialing subscription.
    """
    if not ObjectId.is_valid(new_plan_id):
        raise HTTPException(status_code=400, detail="Invalid new_plan_id")
    new_plan = await get_plan({"_id": ObjectId(new_plan_id)})
    if not new_plan:
        raise HTTPException(status_code=404, detail="New plan not found")
    if new_plan.status != PlanStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="New plan is not active")

    base_price = (
        new_plan.base_price_monthly
        if billing_cycle == BillingCycle.MONTHLY
        else new_plan.base_price_yearly
    )
    valid_discounts: List[DiscountOut] = []
    if discount_ids:
        valid_discounts = await _validate_and_collect_discounts(
            discount_ids, tenant_id, new_plan, base_price
        )

    effective_price = _calculate_effective_price(
        new_plan, billing_cycle, valid_discounts
    )

    applied_ids: List[str] = []
    for d in valid_discounts:
        if d.id:
            await increment_redemptions({"_id": ObjectId(d.id)})
            applied_ids.append(d.id)

    now = int(time.time())
    update_data = SubscriptionUpdate(
        plan_id=new_plan_id,
        status=SubscriptionStatus.ACTIVE,
        billing_cycle=billing_cycle,
        effective_price=effective_price,
        currency=new_plan.currency,
        trial_ends_at=None,
        current_period_start=now,
        current_period_end=_calculate_period_end(now, billing_cycle),
        applied_discount_ids=applied_ids,
        renewal_attempts=0,
        next_retry_at=None,
        cancelled_at=None,
        cancellation_reason=None,
    )
    updated = await update_subscription(
        {"_id": ObjectId(existing_sub_id)},
        update_data,
    )

    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="subscription.plan_changed_via_checkout",
            resource_type="subscription",
            resource_id=existing_sub_id,
            tenant_id=tenant_id,
            details={
                "new_plan_id": new_plan_id,
                "billing_cycle": billing_cycle.value,
                "effective_price": effective_price,
                "applied_discount_ids": applied_ids,
            },
        )
    except Exception:
        pass

    from services.plan_cache_service import invalidate_tenant_plan_cache

    await invalidate_tenant_plan_cache(tenant_id)
    delete_precompute("dashboard.stats", tenant_id=tenant_id)

    return updated


async def cancel_subscription(
    tenant_id: str,
    reason: Optional[str] = None,
    immediate: bool = False,
) -> Optional[SubscriptionOut]:
    """Cancel a tenant's subscription.

    With ``immediate=True``, the tenant is downgraded to the Free plan
    right away (so visitor logging keeps working — see
    ``transition_tenant_to_free_plan`` for the exact side effects).

    With ``immediate=False`` (default), the subscription is flagged as
    cancelled but remains ACTIVE until ``current_period_end``. The
    renewal scheduler will then refuse to renew it and the dunning
    sweep eventually fires the drop-to-free transition naturally.
    """
    current = await retrieve_tenant_active_subscription(tenant_id)
    if not current:
        raise HTTPException(status_code=404, detail="No active subscription to cancel")

    if immediate:
        # The previous "set status=CANCELLED and stop" behaviour broke
        # the tenant entirely (no active sub → 402 SUBSCRIPTION_REQUIRED
        # on every request). Drop to Free instead so visitor logging
        # remains available even after immediate cancellation.
        await update_subscription(
            {"_id": ObjectId(current.id)},
            SubscriptionUpdate(
                status=SubscriptionStatus.CANCELLED,
                cancelled_at=int(time.time()),
                cancellation_reason=reason,
                current_period_end=int(time.time()),
            ),
        )
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="subscription.cancelled",
                resource_type="subscription",
                resource_id=str(current.id),
                tenant_id=tenant_id,
                details={
                    "reason": reason,
                    "immediate": True,
                    "plan_id": str(current.plan_id),
                },
            )
        except Exception:
            pass

        return await transition_tenant_to_free_plan(
            tenant_id=tenant_id,
            reason=reason or "Immediate cancellation",
        )

    # Soft cancel — flag for end-of-period and let renewal handle the
    # drop-to-free at expiry.
    now = int(time.time())
    update_data = SubscriptionUpdate(
        status=SubscriptionStatus.ACTIVE,
        cancelled_at=now,
        cancellation_reason=reason,
    )
    updated = await update_subscription(
        {"_id": ObjectId(current.id)},
        update_data,
    )

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="subscription.cancelled",
            resource_type="subscription",
            resource_id=str(current.id),
            tenant_id=tenant_id,
            details={
                "reason": reason,
                "immediate": False,
                "plan_id": str(current.plan_id),
            },
        )
    except Exception:
        pass

    from services.plan_cache_service import invalidate_tenant_plan_cache

    await invalidate_tenant_plan_cache(tenant_id)
    delete_precompute("dashboard.stats", tenant_id=tenant_id)

    return updated


async def transition_tenant_to_free_plan(
    tenant_id: str,
    *,
    reason: str,
    actor_id: str = "system",
    actor_role: str = "admin",
) -> Optional[SubscriptionOut]:
    """Drop a tenant onto the Free plan.

    Used by:
        * cancel-immediate (replaces the previous "set status=cancelled and
          stop here" behaviour — tenants always retain a working free-tier
          subscription)
        * dunning suspension (replaces the previous "set status=SUSPENDED"
          behaviour — tenants retain access to manual visitor logging)
        * trial conversion failure
        * scripted backfill / migrations

    Side effects (in order):
        1. Mark the tenant's current ACTIVE/TRIALING/PAST_DUE sub as
           ``EXPIRED`` so historical reporting can tell when the paid
           plan ended.
        2. Create a fresh free-plan subscription (price 0, period set
           100 years out so the renewal scheduler ignores it).
        3. Lock down all non-HQ branches to ``inactive`` (Free is single-location).
        4. Invalidate the tenant's plan cache.

    Returns the new free-plan subscription, or None if the free plan
    record is missing (which should never happen post-bootstrap).
    """
    from config.plan_tiers import FREE_PLAN_NAME
    from repositories.plan_repo import get_plan
    from services.branch_service import lock_down_to_hq
    from services.plan_cache_service import invalidate_tenant_plan_cache

    free_plan = await get_plan({"name": FREE_PLAN_NAME})
    if not free_plan or not free_plan.id:
        # The bootstrap has not run yet — nothing we can do here.
        return None

    now = int(time.time())

    # 1. Expire any existing active / trialing / past_due / paused sub.
    existing = await get_subscription(
        {
            "tenant_id": tenant_id,
            "status": {
                "$in": [
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.TRIALING.value,
                    SubscriptionStatus.PAST_DUE.value,
                    SubscriptionStatus.SUSPENDED.value,
                ]
            },
        }
    )
    if existing and existing.id:
        # Skip the expire step if we're already on the free plan.
        if str(existing.plan_id) != str(free_plan.id):
            await update_subscription(
                {"_id": ObjectId(existing.id)},
                SubscriptionUpdate(
                    status=SubscriptionStatus.EXPIRED,
                    cancelled_at=now,
                    cancellation_reason=reason,
                    current_period_end=now,
                ),
            )
        else:
            # Already on free — nothing to transition.
            return existing

    # 2. Create the fresh free subscription. ~100 years in the future
    # keeps the renewal scheduler from picking it up.
    far_future = now + (100 * 365 * 24 * 60 * 60)
    sub_data = SubscriptionCreate(
        tenant_id=tenant_id,
        plan_id=free_plan.id,
        status=SubscriptionStatus.ACTIVE,
        billing_cycle=BillingCycle.MONTHLY,
        effective_price=0.0,
        currency="NGN",
        trial_ends_at=None,
        current_period_start=now,
        current_period_end=far_future,
        admin_notes=f"Auto-downgraded to Free: {reason}",
    )
    new_sub = await create_subscription(sub_data)

    # 3. Lock down branches to HQ AND deactivate departments beyond the
    # new plan's ``max_departments`` cap. Both are best-effort — a
    # failure here doesn't block the downgrade because the access guards
    # in ``branch_service`` / ``department_service`` will still enforce
    # the cap dynamically on every read/write.
    try:
        await lock_down_to_hq(tenant_id)
    except Exception:
        pass
    try:
        from services.department_service import lock_down_to_department_cap

        await lock_down_to_department_cap(tenant_id)
    except Exception:
        pass

    # 4. Invalidate plan + dashboard caches so the next request sees
    # free-tier gates and the slim dashboard payload.
    try:
        await invalidate_tenant_plan_cache(tenant_id)
        delete_precompute("dashboard.stats", tenant_id=tenant_id)
    except Exception:
        pass

    # 5. Audit.
    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="subscription.downgraded_to_free",
            resource_type="subscription",
            resource_id=str(new_sub.id) if new_sub.id else "",
            tenant_id=tenant_id,
            details={
                "reason": reason,
                "previous_subscription_id": existing.id if existing else None,
                "previous_plan_id": existing.plan_id if existing else None,
            },
        )
    except Exception:
        pass

    return new_sub


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

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="subscription.overrides_updated",
            resource_type="subscription",
            resource_id=str(sub_id),
            tenant_id=sub.tenant_id,
            details={
                "feature_overrides_updated": feature_overrides is not None,
                "crud_limit_overrides_updated": crud_limit_overrides is not None,
                "retrieval_quota_overrides_updated": retrieval_quota_overrides
                is not None,
                "tenant_cap_overrides_updated": tenant_cap_overrides is not None,
            },
        )
    except Exception:
        pass

    from services.plan_cache_service import invalidate_tenant_plan_cache

    await invalidate_tenant_plan_cache(sub.tenant_id)
    delete_precompute("dashboard.stats", tenant_id=sub.tenant_id)

    return updated
