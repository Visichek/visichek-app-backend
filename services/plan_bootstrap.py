"""Canonical plan bootstrap + legacy migration.

Run once on app startup (and also exposed as a CLI in
``scripts/migrate_to_canonical_plans.py``) to:

1. Upsert the four canonical plans (Free / Starter / Premium / Enterprise)
   from ``config/plan_tiers.py``.
2. Archive any legacy plan whose name is not in ``CANONICAL_PLAN_NAMES``
   (so it is still readable by historical subscriptions but no longer
   visible in the public catalog and cannot be subscribed to).
3. Backfill every tenant without an active / trialing subscription on a
   canonical plan with a free-plan subscription so the platform's
   "everyone has a plan" invariant holds.

The migration is idempotent — calling it repeatedly is a no-op once the
desired state has been reached.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from bson import ObjectId

from config.plan_tiers import (
    CANONICAL_PLANS,
    CANONICAL_PLAN_NAMES,
    FREE_PLAN_NAME,
    CanonicalPlan,
)
from core.database import db
from repositories.plan_repo import get_plan, get_plans
from repositories.subscription_repo import (
    create_subscription,
    get_subscription,
)
from schemas.plan_schema import (
    PlanCreate,
    PlanOut,
    PlanStatus,
    PlanUpdate,
)
from schemas.subscription_schema import (
    BillingCycle,
    SubscriptionCreate,
    SubscriptionStatus,
)
from services.plan_cache_service import invalidate_tenant_plan_cache

logger = logging.getLogger(__name__)


PLAN_COLLECTION = "plans"
TENANT_COLLECTION = "tenant_companies"


def _canonical_to_plan_create(canonical: CanonicalPlan) -> PlanCreate:
    """Convert a frozen ``CanonicalPlan`` into a ``PlanCreate`` payload."""
    return PlanCreate(
        name=canonical.name,
        display_name=canonical.display_name,
        tier=canonical.tier,
        description=canonical.description,
        status=PlanStatus.ACTIVE,
        base_price_monthly=canonical.base_price_monthly,
        base_price_yearly=canonical.base_price_yearly,
        currency="NGN",
        feature_rules=list(canonical.feature_rules),
        crud_limits=list(canonical.crud_limits),
        retrieval_quotas=list(canonical.retrieval_quotas),
        storage_limits=canonical.storage_limits,
        tenant_caps=canonical.tenant_caps,
        priority_support=canonical.priority_support,
        sla_response_hours=canonical.sla_response_hours,
        custom_branding=canonical.custom_branding,
        api_access=canonical.api_access,
        support_tier=canonical.support_tier,
        is_public=canonical.is_public,
        sort_order=canonical.sort_order,
    )


def _canonical_to_plan_update(canonical: CanonicalPlan) -> PlanUpdate:
    """Convert a frozen ``CanonicalPlan`` into a ``PlanUpdate`` payload.

    We never overwrite admin-adjustable cap fields on existing canonical
    plans — those are the bits an application admin is allowed to tune
    (free-plan visitors/month is the obvious example) and clobbering them
    on every restart would erase admin intent.
    """
    upd = PlanUpdate(
        display_name=canonical.display_name,
        tier=canonical.tier,
        description=canonical.description,
        status=PlanStatus.ACTIVE,
        currency="NGN",
        feature_rules=list(canonical.feature_rules),
        crud_limits=list(canonical.crud_limits),
        retrieval_quotas=list(canonical.retrieval_quotas),
        storage_limits=canonical.storage_limits,
        priority_support=canonical.priority_support,
        sla_response_hours=canonical.sla_response_hours,
        custom_branding=canonical.custom_branding,
        api_access=canonical.api_access,
        support_tier=canonical.support_tier,
        is_public=canonical.is_public,
        sort_order=canonical.sort_order,
    )
    # Pricing — only refresh if the admin has not customised it. Because
    # we store pricing in ``base_price_monthly`` we have no easy "is this
    # admin-set" flag, so we ALWAYS refresh price on Free (which is 0)
    # and leave price as-is on the others (admin may have set per-tenant
    # pricing via subscription overrides, but the catalogue price is
    # frozen here on purpose).
    if canonical.name == FREE_PLAN_NAME:
        upd.base_price_monthly = canonical.base_price_monthly
        upd.base_price_yearly = canonical.base_price_yearly
    return upd


async def ensure_canonical_plans() -> dict[str, str]:
    """Upsert the four canonical plans by name. Idempotent.

    Returns a mapping ``{plan_name: plan_id}`` for the four canonical
    plans so callers (the migration backfill below) can subscribe
    tenants without re-fetching.
    """
    name_to_id: dict[str, str] = {}

    for name, canonical in CANONICAL_PLANS.items():
        existing = await get_plan({"name": name})
        if existing is None:
            try:
                created_dict = _canonical_to_plan_create(canonical).model_dump(
                    mode="json"
                )
                # We bypass ``add_plan`` to avoid the audit-fanout side
                # effect during startup — bootstrap should be silent.
                result = await db[PLAN_COLLECTION].insert_one(created_dict)
                if result.inserted_id is not None:
                    name_to_id[name] = str(result.inserted_id)
                    logger.info(
                        "plan_bootstrap: created canonical plan %s id=%s",
                        name,
                        name_to_id[name],
                    )
            except Exception:
                logger.exception("plan_bootstrap: failed to create %s", name)
            continue

        # Existing — refresh the immutable fields. We deliberately skip
        # ``tenant_caps`` so an admin-tuned visitors/month cap on Free
        # survives a redeploy.
        if existing.id:
            name_to_id[name] = existing.id
            try:
                update_dict = {
                    k: v
                    for k, v in _canonical_to_plan_update(canonical)
                    .model_dump(mode="json")
                    .items()
                    if v is not None
                }
                if update_dict:
                    await db[PLAN_COLLECTION].update_one(
                        {"_id": ObjectId(existing.id)},
                        {"$set": update_dict},
                    )
                    logger.info(
                        "plan_bootstrap: refreshed canonical plan %s id=%s",
                        name,
                        existing.id,
                    )
            except Exception:
                logger.exception("plan_bootstrap: failed to refresh %s", name)

    return name_to_id


async def archive_legacy_plans() -> int:
    """Archive any plan whose tier is NOT enterprise and whose name is
    NOT a singleton (``"free"``, ``"starter"``, ``"premium"``).

    Enterprise plans are bespoke — many can coexist, each with a unique
    slug — so they are NEVER auto-archived. Singletons must stay active
    because every tenant transitively depends on the free plan. The
    only thing this rule fires on is legacy / experimental plans like
    the historical ``"professional"`` or any one-off draft.

    Existing subscriptions on archived plans keep working until they
    expire; ``subscribe_tenant`` simply blocks NEW subscriptions to
    archived plans. Returns the number of plans archived.
    """
    archived_count = 0
    legacy_plans: List[PlanOut] = await get_plans(
        filter_dict={
            "name": {"$nin": list(CANONICAL_PLAN_NAMES)},
            "tier": {"$ne": "enterprise"},
        },
        start=0,
        stop=1000,
    )
    for plan in legacy_plans:
        if not plan.id:
            continue
        if plan.status == PlanStatus.ARCHIVED:
            continue
        try:
            await db[PLAN_COLLECTION].update_one(
                {"_id": ObjectId(plan.id)},
                {
                    "$set": {
                        "status": PlanStatus.ARCHIVED.value,
                        "is_public": False,
                        "last_updated": int(time.time()),
                    }
                },
            )
            archived_count += 1
            logger.info(
                "plan_bootstrap: archived legacy plan %s (id=%s, tier=%s)",
                plan.name,
                plan.id,
                plan.tier.value if hasattr(plan.tier, "value") else plan.tier,
            )
        except Exception:
            logger.exception(
                "plan_bootstrap: failed to archive legacy plan %s", plan.name
            )
    return archived_count


async def _create_free_subscription_for_tenant(
    tenant_id: str, free_plan_id: str, *, reason: Optional[str] = None
) -> None:
    """Create a free-plan ``SubscriptionCreate`` for one tenant.

    The free plan never expires (period_end is set far enough in the
    future that the renewal scheduler never picks it up — 100 years).
    This keeps the existing renewal/dunning code paths untouched.
    """
    now = int(time.time())
    far_future = now + (100 * 365 * 24 * 60 * 60)

    sub_data = SubscriptionCreate(
        tenant_id=tenant_id,
        plan_id=free_plan_id,
        status=SubscriptionStatus.ACTIVE,
        billing_cycle=BillingCycle.MONTHLY,
        effective_price=0.0,
        currency="NGN",
        trial_ends_at=None,
        current_period_start=now,
        current_period_end=far_future,
        admin_notes=(reason or "Auto-provisioned free plan"),
    )
    await create_subscription(sub_data)
    await invalidate_tenant_plan_cache(tenant_id)


async def ensure_tenant_default_subscription(
    tenant_id: str,
    *,
    free_plan_id: Optional[str] = None,
) -> bool:
    """Guarantee the tenant has at least one ACTIVE / TRIALING subscription.

    Returns True if a new free-plan subscription was created, False if
    the tenant already had an active subscription.
    """
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
        return False

    plan_id = free_plan_id
    if plan_id is None:
        free_plan = await get_plan({"name": FREE_PLAN_NAME})
        if not free_plan or not free_plan.id:
            logger.error(
                "plan_bootstrap: cannot subscribe tenant %s — free plan missing",
                tenant_id,
            )
            return False
        plan_id = free_plan.id

    try:
        await _create_free_subscription_for_tenant(
            tenant_id=tenant_id,
            free_plan_id=plan_id,
            reason="Auto-provisioned at first run",
        )
        logger.info(
            "plan_bootstrap: created free subscription for tenant %s", tenant_id
        )
        return True
    except Exception:
        logger.exception(
            "plan_bootstrap: failed to create free subscription for %s", tenant_id
        )
        return False


async def migrate_tenants_to_free(name_to_id: Optional[dict[str, str]] = None) -> int:
    """Backfill: every tenant without an active sub gets the free plan.

    Tenants whose only active sub is on a now-archived legacy plan are
    LEFT alone — those subscriptions remain ACTIVE until they expire
    naturally, at which point the renewal/dunning path will drop them
    to free via ``transition_tenant_to_free_plan``. This avoids ripping
    paying customers off their plan mid-cycle.

    Returns the count of free subscriptions created.
    """
    free_plan_id: str
    if name_to_id is None:
        free_plan = await get_plan({"name": FREE_PLAN_NAME})
        if not free_plan or not free_plan.id:
            logger.error("plan_bootstrap: free plan missing — aborting backfill")
            return 0
        free_plan_id = free_plan.id
    else:
        candidate = name_to_id.get(FREE_PLAN_NAME)
        if not candidate:
            logger.error("plan_bootstrap: free plan id missing — aborting backfill")
            return 0
        free_plan_id = candidate

    created = 0
    cursor = db[TENANT_COLLECTION].find(
        {"is_active": {"$ne": False}},
        projection={"_id": 1},
    )
    async for tenant_doc in cursor:
        tenant_id = str(tenant_doc.get("_id"))
        try:
            did_create = await ensure_tenant_default_subscription(
                tenant_id=tenant_id, free_plan_id=free_plan_id
            )
            if did_create:
                created += 1
        except Exception:
            logger.exception(
                "plan_bootstrap: backfill failed for tenant %s", tenant_id
            )
    if created:
        logger.info(
            "plan_bootstrap: backfilled %s tenant(s) onto free plan", created
        )
    return created


async def run_full_bootstrap() -> dict[str, Any]:
    """One-shot: upsert canonical plans, archive legacy, backfill tenants.

    Returns a small report dict suitable for logging or the admin CLI.
    """
    name_to_id = await ensure_canonical_plans()
    archived = await archive_legacy_plans()
    created = await migrate_tenants_to_free(name_to_id=name_to_id)
    return {
        "canonical_plans": name_to_id,
        "legacy_plans_archived": archived,
        "tenants_subscribed_to_free": created,
    }
