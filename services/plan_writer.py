"""Queued write handlers + precompute loaders for subscription plans.

Every plan mutation enqueues the existing ``cache.plan.invalidate_plan_fanout``
task so tenant plan caches refresh globally. Without that fanout, all
subscribers to the changed plan would see stale data for 5 minutes.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.plan_schema import PlanCreate, PlanUpdate
from services.plan_service import (
    activate_plan,
    add_plan,
    archive_plan,
    clone_plan,
    remove_plan,
    retrieve_plans,
    update_plan_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_plan_cache_fanout(plan_id: str) -> None:
    """Queue the existing plan-cache fanout so subscribers refresh."""
    if not plan_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="cache.plan.invalidate_plan_fanout",
            payload={"plan_id": plan_id},
        )
    except Exception:
        logger.warning(
            "plan_writer: plan cache fanout enqueue failed plan_id=%s",
            plan_id,
            exc_info=True,
        )


def _enqueue_list_refresh() -> None:
    """Refresh both list precomputes — admin and public.

    The route-layer ``GET /v1/plans`` endpoint reads
    ``precomputed:global:plans.list`` for unfiltered admin views and
    ``precomputed:global:plans.public_list`` when ``public_only=true``.
    Refreshing only one leaves the other stale for up to
    ``PRECOMPUTE_TTL_SECONDS`` (5 min), so an admin can activate a plan
    and still see it as draft on the public catalogue.
    """
    qm = QueueManager.get_instance()
    for resource in ("plans.list", "plans.public_list"):
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "plan_writer: list refresh enqueue failed resource=%s",
                resource,
                exc_info=True,
            )


@write_handler(
    "plan.create", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    plan = PlanCreate(**data)
    result = await add_plan(plan_data=plan, preassigned_id=resource_id)
    _enqueue_list_refresh()
    return {"id": result.id, "name": result.name}


@write_handler(
    "plan.update", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    upd = PlanUpdate(**data)
    result = await update_plan_by_id(plan_id=resource_id, plan_data=upd)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id}


@write_handler(
    "plan.activate", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_activate(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    result = await activate_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id, "status": "active"}


@write_handler(
    "plan.archive", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_archive(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    result = await archive_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id, "status": "archived"}


@write_handler(
    "plan.clone", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_clone(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    new_name = data.get("new_name", "") or ""
    new_display_name = data.get("new_display_name", "") or ""
    # ``resource_id`` here is the *source* plan id, not the new one.
    # clone_plan creates a new draft plan with a fresh _id.
    result = await clone_plan(
        source_plan_id=resource_id,
        new_name=new_name,
        new_display_name=new_display_name,
    )
    _enqueue_list_refresh()
    return {"id": result.id, "name": result.name}


@write_handler(
    "plan.delete", invalidates=[
        "plans.list",
        "plans.public_list",
        # plan_summary embedded on subscription views
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await remove_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": resource_id, "deleted": True}


@write_handler(
    "plan.set_feature", invalidates=[
        "plans.list",
        "plans.public_list",
        "subscriptions.list",
        "subscription.active",
    ]
)
async def _plan_set_feature(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Toggle a single named feature on a plan.

    Backed by ``services.plan_feature_service.set_plan_feature``. The
    payload carries ``feature_key`` + ``enabled`` plus the standard
    actor envelope. Plan-cache fanout runs so subscribed tenants see
    the new feature gate within seconds rather than waiting on the
    5-minute cache TTL.
    """
    from services.plan_feature_service import set_plan_feature

    feature_key = data.pop("feature_key", "") or ""
    enabled = bool(data.pop("enabled", False))
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or "admin"
    request_id = data.pop("_request_id", None)

    result = await set_plan_feature(
        plan_id=resource_id,
        feature_key=feature_key,
        enabled=enabled,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {
        "id": result.id,
        "feature_key": feature_key,
        "enabled": enabled,
    }


@register_precompute("plans.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_plans_list(_tenant_id: str) -> List[dict[str, Any]]:
    plans = await retrieve_plans(start=0, stop=100)
    return [p.model_dump(mode="json", by_alias=True) for p in plans]


@register_precompute("plans.public_list", scope=PrecomputeScope.GLOBAL)
async def _precompute_plans_public_list(_tenant_id: str) -> List[dict[str, Any]]:
    plans = await retrieve_plans(public_only=True, start=0, stop=100)
    return [p.model_dump(mode="json", by_alias=True) for p in plans]
