"""Queued write handlers for subscriptions.

Each writer returns the concrete subscription id in its result so the
frontend can resolve the real id via ``GET /v1/jobs/{job_id}`` even
though the ``enqueue_write`` 202 response contains a speculative
pre-generated id (subscribe_tenant assigns its own).

Tenant plan cache invalidation is preserved inside the existing
service functions — we don't need to enqueue it separately.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from repositories.subscription_repo import get_subscription as _get_subscription_doc
from services.subscription_service import (
    cancel_subscription,
    change_plan,
    retrieve_subscriptions_with_details,
    retrieve_tenant_active_subscription,
    subscribe_tenant,
    update_subscription_overrides,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    qm = QueueManager.get_instance()
    resources = ["subscriptions.list"]
    if tenant_id:
        resources.append("subscription.active")
    for resource in resources:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": tenant_id or "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "subscription_writer: refresh enqueue failed tenant=%s resource=%s",
                tenant_id,
                resource,
                exc_info=True,
            )


@write_handler(
    "subscription.create",
    invalidates=[
        "subscriptions.list",
        "subscription.active",
        # subscription_summary embedded on tenant + invoice views
        "tenants.list",
    ],
)
async def _subscription_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    result = await subscribe_tenant(
        tenant_id=tenant_id,
        plan_id=data["plan_id"],
        billing_cycle=data.get("billing_cycle", "monthly"),
        discount_ids=data.get("discount_ids", []) or [],
        trial_days=data.get("trial_days", 0) or 0,
        admin_notes=data.get("admin_notes"),
        feature_overrides=data.get("feature_overrides"),
        crud_limit_overrides=data.get("crud_limit_overrides"),
        retrieval_quota_overrides=data.get("retrieval_quota_overrides"),
        tenant_cap_overrides=data.get("tenant_cap_overrides"),
    )
    _enqueue_refresh(tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "plan_id": result.plan_id,
        "status": result.status.value
        if hasattr(result.status, "value")
        else result.status,
    }


@write_handler(
    "subscription.change_plan",
    invalidates=[
        "subscriptions.list",
        "subscription.active",
        # subscription_summary embedded on tenant + invoice views
        "tenants.list",
    ],
)
async def _subscription_change_plan(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    result = await change_plan(
        tenant_id=tenant_id,
        new_plan_id=data["new_plan_id"],
        billing_cycle=data.get("billing_cycle"),
    )
    _enqueue_refresh(tenant_id)
    return {
        "id": result.id if result else None,
        "plan_id": result.plan_id if result else data.get("new_plan_id"),
    }


@write_handler(
    "subscription.cancel",
    invalidates=[
        "subscriptions.list",
        "subscription.active",
        # subscription_summary embedded on tenant + invoice views
        "tenants.list",
    ],
)
async def _subscription_cancel(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    result = await cancel_subscription(
        tenant_id=tenant_id,
        reason=data.get("reason"),
        immediate=bool(data.get("immediate", False)),
    )
    _enqueue_refresh(tenant_id)
    return {
        "id": result.id if result else None,
        "status": result.status.value
        if result and hasattr(result.status, "value")
        else None,
    }


@write_handler(
    "subscription.update_overrides",
    invalidates=[
        "subscriptions.list",
        "subscription.active",
        # subscription_summary embedded on tenant + invoice views
        "tenants.list",
    ],
)
async def _subscription_update_overrides(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    result = await update_subscription_overrides(
        sub_id=resource_id,
        feature_overrides=data.get("feature_overrides"),
        crud_limit_overrides=data.get("crud_limit_overrides"),
        retrieval_quota_overrides=data.get("retrieval_quota_overrides"),
        tenant_cap_overrides=data.get("tenant_cap_overrides"),
    )
    tenant_id = result.tenant_id if result else ""
    _enqueue_refresh(tenant_id)
    return {"id": result.id if result else resource_id}


@write_handler(
    "subscription.bulk_cancel",
    invalidates=[
        "subscriptions.list",
        "subscription.active",
        "tenants.list",
    ],
)
async def _subscription_bulk_cancel(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Bulk cancel by subscription_id.

    Each id is resolved to its tenant via the bare repo (``get_subscription``)
    and then cancelled via the standard ``cancel_subscription`` flow so
    audit + plan-cache invalidation cascade correctly. The bulk path skips
    the enriched read function deliberately — the per-id summary lookups
    are pure waste for a write path that only needs the tenant id.
    """
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    reason = extras.get("reason") or None
    immediate = bool(extras.get("immediate", False))
    tenants_seen: set[str] = set()

    async def _handle(subscription_id: str) -> dict[str, Any]:
        from bson import ObjectId as _OID

        if not _OID.is_valid(subscription_id):
            raise ValueError("Subscription not found")
        sub = await _get_subscription_doc({"_id": _OID(subscription_id)})
        if not sub or not sub.tenant_id:
            raise ValueError("Subscription not found")
        tenants_seen.add(sub.tenant_id)
        result = await cancel_subscription(
            tenant_id=sub.tenant_id, reason=reason, immediate=immediate
        )
        return {
            "id": result.id if result else subscription_id,
            "tenant_id": sub.tenant_id,
        }

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    for tid in tenants_seen:
        _enqueue_refresh(tid)
    return out


@register_precompute("subscriptions.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_subscriptions_list(_tenant_id: str) -> List[Any]:
    subs = await retrieve_subscriptions_with_details(start=0, stop=100)
    return [
        s.model_dump(mode="json", by_alias=True) if hasattr(s, "model_dump") else s
        for s in subs
    ]


@register_precompute("subscription.active", scope=PrecomputeScope.TENANT)
async def _precompute_subscription_active(tenant_id: str) -> Any:
    """Same enrichment as ``_load_active_subscription`` in the route.

    Cache hits and cache misses MUST return the same shape, otherwise
    the frontend's "is the tenant on the free fallback?" check breaks
    intermittently depending on whether the precompute key is warm.
    """
    from bson import ObjectId as _BsonObjectId

    from config.plan_tiers import FREE_PLAN_NAME
    from repositories.plan_repo import get_plan

    sub = await retrieve_tenant_active_subscription(tenant_id)
    if not sub:
        return None

    payload: dict[str, Any] = sub.model_dump(mode="json", by_alias=True)

    plan_summary: Any = None
    is_free_fallback = False
    if sub.plan_id and _BsonObjectId.is_valid(sub.plan_id):
        plan = await get_plan({"_id": _BsonObjectId(sub.plan_id)})
        if plan is not None:
            plan_summary = {
                "id": plan.id,
                "name": plan.name,
                "displayName": plan.display_name,
                "tier": plan.tier.value if hasattr(plan.tier, "value") else plan.tier,
                "basePriceMonthly": plan.base_price_monthly,
                "basePriceYearly": plan.base_price_yearly,
                "currency": plan.currency,
            }
            is_free_fallback = plan.name == FREE_PLAN_NAME and bool(
                (sub.admin_notes or "").startswith("Auto-downgraded")
            )

    payload["plan"] = plan_summary
    payload["isFreeFallback"] = is_free_fallback
    return payload
