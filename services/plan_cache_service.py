from __future__ import annotations

import json
from typing import Any, Optional, cast

from core.redis_cache import cache_db

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
