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
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": "", "resource": "plans.list"},
        )
    except Exception:
        logger.warning("plan_writer: list refresh enqueue failed", exc_info=True)


@write_handler("plan.create")
async def _plan_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    plan = PlanCreate(**data)
    result = await add_plan(plan_data=plan, preassigned_id=resource_id)
    _enqueue_list_refresh()
    return {"id": result.id, "name": result.name}


@write_handler("plan.update")
async def _plan_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    upd = PlanUpdate(**data)
    result = await update_plan_by_id(plan_id=resource_id, plan_data=upd)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id}


@write_handler("plan.activate")
async def _plan_activate(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    result = await activate_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id, "status": "active"}


@write_handler("plan.archive")
async def _plan_archive(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    result = await archive_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id, "status": "archived"}


@write_handler("plan.clone")
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


@write_handler("plan.delete")
async def _plan_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await remove_plan(plan_id=resource_id)
    _enqueue_plan_cache_fanout(resource_id)
    _enqueue_list_refresh()
    return {"id": resource_id, "deleted": True}


@register_precompute("plans.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_plans_list(_tenant_id: str) -> List[dict[str, Any]]:
    plans = await retrieve_plans(start=0, stop=100)
    return [p.model_dump(mode="json", by_alias=True) for p in plans]


@register_precompute("plans.public_list", scope=PrecomputeScope.GLOBAL)
async def _precompute_plans_public_list(_tenant_id: str) -> List[dict[str, Any]]:
    plans = await retrieve_plans(public_only=True, start=0, stop=100)
    return [p.model_dump(mode="json", by_alias=True) for p in plans]
