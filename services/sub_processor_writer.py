"""Queued write handlers + precompute loader for sub-processors."""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate
from services.sub_processor_service import (
    add_sub_processor,
    remove_sub_processor,
    retrieve_sub_processors,
    update_sub_processor_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "sub_processors.list"},
        )
    except Exception:
        logger.warning(
            "sub_processor_writer: precompute refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("sub_processor.create")
async def _sub_processor_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    sp_data = SubProcessorCreate(**data)
    result = await add_sub_processor(sp_data=sp_data, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id, "provider": result.provider}


@write_handler("sub_processor.update")
async def _sub_processor_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = SubProcessorUpdate(**data)
    result = await update_sub_processor_by_id(
        sp_id=resource_id, tenant_id=tenant_id, sp_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "provider": result.provider}


@write_handler("sub_processor.delete")
async def _sub_processor_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_sub_processor(sp_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@register_precompute("sub_processors.list", scope=PrecomputeScope.TENANT)
async def _precompute_sub_processors_list(tenant_id: str) -> List[dict[str, Any]]:
    sps = await retrieve_sub_processors(tenant_id=tenant_id)
    return [sp.model_dump(mode="json", by_alias=True) for sp in sps]
