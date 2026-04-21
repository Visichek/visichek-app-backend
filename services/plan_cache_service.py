from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional, cast

from core.redis_cache import cache_db


def _resolve_support_tier_value(sub: Any, plan: Any) -> str:
    """Resolve the effective support tier as a string (plan default, sub override wins)."""
    override = getattr(sub, "support_tier_override", None)
    if override is not None:
        return override.value if hasattr(override, "value") else str(override)
    tier = getattr(plan, "support_tier", None)
    if tier is not None:
        return tier.value if hasattr(tier, "value") else str(tier)
    return "none"

# Cache TTL in seconds (5 minutes for plan data, good balance of freshness vs performance)
PLAN_CACHE_TTL = 300
TENANT_PLAN_PREFIX = "tenant_plan:"
PLAN_DATA_PREFIX = "plan_data:"


async def get_cached_tenant_plan(tenant_id: str) -> Optional[dict]:
    """Get cached resolved plan data for a tenant (plan + subscription overrides merged).
    Returns None on cache miss.
    """
    try:
        raw = cast(Any, cache_db.get(f"{TENANT_PLAN_PREFIX}{tenant_id}"))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def set_cached_tenant_plan(tenant_id: str, plan_data: dict) -> None:
    """Cache resolved plan data for a tenant."""
    try:
        cache_db.setex(
            f"{TENANT_PLAN_PREFIX}{tenant_id}",
            PLAN_CACHE_TTL,
            json.dumps(plan_data),
        )
    except Exception:
        pass  # Non-critical: enforce from DB on cache failure


async def invalidate_tenant_plan_cache(tenant_id: str) -> None:
    """Invalidate a tenant's cached plan data (called on subscription changes)."""
    try:
        cache_db.delete(f"{TENANT_PLAN_PREFIX}{tenant_id}")
    except Exception:
        pass


async def invalidate_plan_cache(plan_id: str) -> None:
    """Invalidate cached data for a specific plan.
    Also invalidate all tenants using this plan (brute force, but plans rarely change).
    """
    try:
        cache_db.delete(f"{PLAN_DATA_PREFIX}{plan_id}")
        # Scan for tenant keys and invalidate those on the plan
        # This is fine because plan changes are rare admin operations
        cursor = 0
        while True:
            cursor, keys = cast(
                Any, cache_db.scan(cursor, match=f"{TENANT_PLAN_PREFIX}*", count=100)
            )
            for key in keys:
                try:
                    raw = cast(Any, cache_db.get(key))
                    if raw:
                        data = json.loads(raw)
                        if data.get("plan_id") == plan_id:
                            cache_db.delete(key)
                except Exception:
                    continue
            if cursor == 0:
                break
    except Exception:
        pass


async def resolve_tenant_plan(tenant_id: str) -> Optional[dict]:
    """Resolve the effective plan for a tenant: plan defaults + subscription overrides.
    Uses cache with fallback to DB.
    Returns a dict with all enforcement data needed by the middleware.
    """
    # Check cache first
    cached = await get_cached_tenant_plan(tenant_id)
    if cached:
        return cached

    # Cache miss — resolve from DB
    from repositories.subscription_repo import get_subscription
    from repositories.plan_repo import get_plan
    from bson import ObjectId
    from schemas.subscription_schema import SubscriptionStatus

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
    if not sub:
        return None

    if not ObjectId.is_valid(sub.plan_id):
        return None
    plan = await get_plan({"_id": ObjectId(sub.plan_id)})
    if not plan:
        return None

    # Build resolved plan data
    plan_dict = plan.model_dump(mode="json")

    # Merge subscription overrides
    resolved = {
        "plan_id": sub.plan_id,
        "plan_name": plan.name,
        "plan_display_name": plan.display_name,
        "tier": plan.tier,
        "subscription_id": sub.id,
        "subscription_status": sub.status,
        "tenant_id": tenant_id,
        # Feature rules: subscription overrides merge with plan defaults
        "feature_rules": _merge_feature_rules(
            plan_dict.get("feature_rules", []),
            sub.feature_overrides,
        ),
        # CRUD limits: subscription overrides merge with plan defaults
        "crud_limits": _merge_crud_limits(
            plan_dict.get("crud_limits", []),
            sub.crud_limit_overrides,
        ),
        # Retrieval quotas: subscription overrides merge
        "retrieval_quotas": _merge_retrieval_quotas(
            plan_dict.get("retrieval_quotas", []),
            sub.retrieval_quota_overrides,
        ),
        # Storage limits from plan
        "storage_limits": plan_dict.get("storage_limits", {}),
        # Tenant caps: subscription overrides merge with plan defaults
        "tenant_caps": _merge_tenant_caps(
            plan_dict.get("tenant_caps", {}),
            sub.tenant_cap_overrides,
        ),
        # Feature flags
        "priority_support": plan.priority_support,
        "custom_branding": plan.custom_branding,
        "api_access": plan.api_access,
        # Support tier (plan default, subscription override wins)
        "support_tier": _resolve_support_tier_value(sub, plan),
        # Billing info
        "effective_price": sub.effective_price,
        "billing_cycle": sub.billing_cycle,
        "current_period_end": sub.current_period_end,
        "trial_ends_at": sub.trial_ends_at,
    }

    # Cache the resolved plan
    await set_cached_tenant_plan(tenant_id, resolved)
    return resolved


def _merge_feature_rules(plan_rules: list, overrides: Optional[dict]) -> list:
    """Merge plan feature rules with subscription-level overrides.
    Overrides are keyed by endpoint_pattern: {"enabled": bool}.
    """
    if not overrides:
        return plan_rules

    merged = []
    for rule in plan_rules:
        pattern = rule.get("endpoint_pattern", "")
        if pattern in overrides:
            rule = {**rule, **overrides[pattern]}
        merged.append(rule)
    return merged


def _merge_crud_limits(plan_limits: list, overrides: Optional[dict]) -> list:
    """Merge plan CRUD limits with subscription-level overrides.
    Overrides are keyed by collection: {"max_create": N, ...}.
    """
    if not overrides:
        return plan_limits

    merged = []
    for limit in plan_limits:
        collection = limit.get("collection", "")
        if collection in overrides:
            limit = {**limit, **overrides[collection]}
        merged.append(limit)
    return merged


def _merge_retrieval_quotas(plan_quotas: list, overrides: Optional[dict]) -> list:
    """Merge plan retrieval quotas with subscription-level overrides."""
    if not overrides:
        return plan_quotas

    merged = []
    for quota in plan_quotas:
        collection = quota.get("collection", "")
        if collection in overrides:
            quota = {**quota, **overrides[collection]}
        merged.append(quota)
    return merged


def _merge_tenant_caps(plan_caps: dict, overrides: Optional[dict]) -> dict:
    """Merge plan tenant caps with subscription-level overrides."""
    if not overrides:
        return plan_caps
    return {**plan_caps, **overrides}


def _build_resolved_from_raw(sub: dict, plan: dict, tenant_id: str) -> dict:
    """Build the resolved plan dict from raw Mongo documents.

    Same merge rules as ``resolve_tenant_plan`` but operating on plain dicts —
    used by the bulk path to avoid constructing Pydantic models for every row.
    """
    from bson import ObjectId

    plan_id = sub.get("plan_id")
    sub_id_raw = sub.get("_id")
    sub_id = str(sub_id_raw) if isinstance(sub_id_raw, ObjectId) else sub_id_raw

    return {
        "plan_id": plan_id,
        "plan_name": plan.get("name"),
        "plan_display_name": plan.get("display_name"),
        "tier": plan.get("tier"),
        "subscription_id": sub_id,
        "subscription_status": sub.get("status"),
        "tenant_id": tenant_id,
        "feature_rules": _merge_feature_rules(
            plan.get("feature_rules", []),
            sub.get("feature_overrides"),
        ),
        "crud_limits": _merge_crud_limits(
            plan.get("crud_limits", []),
            sub.get("crud_limit_overrides"),
        ),
        "retrieval_quotas": _merge_retrieval_quotas(
            plan.get("retrieval_quotas", []),
            sub.get("retrieval_quota_overrides"),
        ),
        "storage_limits": plan.get("storage_limits", {}),
        "tenant_caps": _merge_tenant_caps(
            plan.get("tenant_caps", {}),
            sub.get("tenant_cap_overrides"),
        ),
        "priority_support": plan.get("priority_support"),
        "custom_branding": plan.get("custom_branding"),
        "api_access": plan.get("api_access"),
        "support_tier": (
            sub.get("support_tier_override")
            or plan.get("support_tier")
            or "none"
        ),
        "effective_price": sub.get("effective_price"),
        "billing_cycle": sub.get("billing_cycle"),
        "current_period_end": sub.get("current_period_end"),
        "trial_ends_at": sub.get("trial_ends_at"),
    }


async def resolve_tenant_plans_bulk(tenant_ids: List[str]) -> dict[str, dict]:
    """Resolve effective plans for many tenants in batched I/O.

    Replaces the per-tenant loop of ``resolve_tenant_plan`` with:
      1. One Redis ``MGET`` for all cached plans (off the event loop).
      2. One Mongo ``find`` on ``subscriptions`` for cache misses.
      3. One Mongo ``find`` on ``plans`` for the referenced plan ids.
      4. One pipelined Redis write to repopulate the misses.

    This turns the previous N+1 blocking pattern into O(1) round trips
    regardless of tenant count, which is the main win for list endpoints.
    Cache key format and payload shape are identical to
    ``resolve_tenant_plan`` so a hit from one function is reusable by the
    other.
    """
    if not tenant_ids:
        return {}

    keys = [f"{TENANT_PLAN_PREFIX}{tid}" for tid in tenant_ids]

    try:
        cached_raw = cast(
            List[Optional[str]], await asyncio.to_thread(cache_db.mget, keys)
        )
    except Exception:
        cached_raw = [None] * len(keys)

    result: dict[str, dict] = {}
    missing: List[str] = []
    for tid, raw in zip(tenant_ids, cached_raw or []):
        if raw:
            try:
                result[tid] = json.loads(cast(Any, raw))
                continue
            except Exception:
                pass
        missing.append(tid)

    if not missing:
        return result

    # Bulk-fetch active/trialing subscriptions for the missing tenants.
    from core.database import db
    from schemas.subscription_schema import SubscriptionStatus

    sub_filter = {
        "tenant_id": {"$in": missing},
        "status": {
            "$in": [
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIALING.value,
            ]
        },
    }
    subs_by_tenant: dict[str, dict] = {}
    plan_ids: set[str] = set()
    async for sub in db["subscriptions"].find(sub_filter):
        tid = sub.get("tenant_id")
        if not tid:
            continue
        # If a tenant has multiple active/trialing subs somehow, keep the first.
        subs_by_tenant.setdefault(tid, sub)
        pid = sub.get("plan_id")
        if pid:
            plan_ids.add(pid)

    if not subs_by_tenant:
        return result

    # Bulk-fetch the plans referenced by those subscriptions.
    from bson import ObjectId

    plan_oids = [ObjectId(pid) for pid in plan_ids if ObjectId.is_valid(pid)]
    plans_by_id: dict[str, dict] = {}
    if plan_oids:
        async for plan in db["plans"].find({"_id": {"$in": plan_oids}}):
            plans_by_id[str(plan["_id"])] = plan

    if not plans_by_id:
        return result

    # Merge + stage writes.
    to_cache: dict[str, str] = {}
    for tid, sub in subs_by_tenant.items():
        plan = plans_by_id.get(str(sub.get("plan_id")))
        if not plan:
            continue
        resolved = _build_resolved_from_raw(sub, plan, tid)
        result[tid] = resolved
        try:
            to_cache[f"{TENANT_PLAN_PREFIX}{tid}"] = json.dumps(resolved)
        except Exception:
            # A non-serialisable value should never slip through, but we'd
            # rather serve the request than blow up on a cache write.
            continue

    if to_cache:

        def _pipeline_write() -> None:
            pipe = cache_db.pipeline()
            for key, value in to_cache.items():
                pipe.setex(key, PLAN_CACHE_TTL, value)
            pipe.execute()

        try:
            await asyncio.to_thread(_pipeline_write)
        except Exception:
            pass

    return result
