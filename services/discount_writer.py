"""Queued write handlers + precompute loader for discounts (application admin)."""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.discount_schema import DiscountCreate, DiscountUpdate
from services.discount_service import (
    add_discount,
    disable_discount,
    remove_discount,
    retrieve_discounts,
    update_discount_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh() -> None:
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": "", "resource": "discounts.list"},
        )
    except Exception:
        logger.warning("discount_writer: list refresh enqueue failed", exc_info=True)


@write_handler("discount.create", invalidates=["discounts.list"])
async def _discount_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    discount = DiscountCreate(**data)
    result = await add_discount(discount_data=discount, preassigned_id=resource_id)
    _enqueue_list_refresh()
    await _fan_out_discount_notifications(result)
    return {"id": result.id, "code": result.code}


async def _fan_out_discount_notifications(result: Any) -> None:
    """Notify + email eligible tenants that a new discount is available.

    Only active discounts are worth announcing — a discount created in a
    disabled state shouldn't ping anyone. Fire-and-forget: never raises.
    """
    from schemas.discount_schema import DiscountStatus
    from services.discount_notification_service import enqueue_discount_announcement

    try:
        if getattr(result, "status", None) != DiscountStatus.ACTIVE:
            return
        scope = result.scope.value if hasattr(result.scope, "value") else result.scope
        d_type = (
            result.discount_type.value
            if hasattr(result.discount_type, "value")
            else result.discount_type
        )
        # Hand off to the queue: this only enqueues a coordinator task, so the
        # write commits fast even for a global discount that will reach every
        # tenant. The coordinator paginates + fans out into bounded batches.
        enqueue_discount_announcement(
            discount_id=result.id or "",
            code=result.code,
            name=result.name,
            scope=scope,
            discount_type=d_type,
            value=result.value,
            target_tenant_id=result.target_tenant_id,
            target_plan_ids=result.target_plan_ids,
            valid_until=result.valid_until,
        )
    except Exception:
        logger.warning(
            "discount_writer: discount-available fan-out failed", exc_info=True
        )


@write_handler("discount.update", invalidates=["discounts.list"])
async def _discount_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    upd = DiscountUpdate(**data)
    result = await update_discount_by_id(discount_id=resource_id, data=upd)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id}


@write_handler("discount.disable", invalidates=["discounts.list"])
async def _discount_disable(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    result = await disable_discount(discount_id=resource_id)
    _enqueue_list_refresh()
    return {"id": result.id if result else resource_id, "status": "disabled"}


@write_handler("discount.delete", invalidates=["discounts.list"])
async def _discount_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await remove_discount(discount_id=resource_id)
    _enqueue_list_refresh()
    return {"id": resource_id, "deleted": True}


@write_handler("discount.bulk_disable", invalidates=["discounts.list"])
async def _discount_bulk_disable(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))

    async def _handle(discount_id: str) -> dict[str, Any]:
        result = await disable_discount(discount_id=discount_id)
        return {"id": result.id if result else discount_id, "status": "disabled"}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    _enqueue_list_refresh()
    return out


@write_handler("discount.bulk_delete", invalidates=["discounts.list"])
async def _discount_bulk_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))

    async def _handle(discount_id: str) -> dict[str, Any]:
        await remove_discount(discount_id=discount_id)
        return {"id": discount_id, "deleted": True}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    _enqueue_list_refresh()
    return out


@register_precompute("discounts.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_discounts_list(_tenant_id: str) -> List[dict[str, Any]]:
    discounts = await retrieve_discounts(start=0, stop=100)
    return [d.model_dump(mode="json", by_alias=True) for d in discounts]
