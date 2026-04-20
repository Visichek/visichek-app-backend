"""Queued write handlers + precompute loader for DSRs."""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate
from services.data_subject_request_service import (
    add_dsr,
    retrieve_dsrs,
    update_dsr_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "dsr.list"},
        )
    except Exception:
        logger.warning(
            "dsr_writer: refresh enqueue failed tenant=%s", tenant_id, exc_info=True
        )


@write_handler("dsr.create")
async def _dsr_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    dsr = DSRCreate(**data)
    result = await add_dsr(dsr_data=dsr, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "request_type": result.request_type,
        "status": result.status,
    }


@write_handler("dsr.update")
async def _dsr_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = DSRUpdate(**data)
    result = await update_dsr_by_id(
        dsr_id=resource_id, tenant_id=tenant_id, dsr_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "status": result.status}


@register_precompute("dsr.list", scope=PrecomputeScope.TENANT)
async def _precompute_dsr_list(tenant_id: str) -> List[dict[str, Any]]:
    dsrs = await retrieve_dsrs(tenant_id=tenant_id, start=0, stop=100)
    return [d.model_dump(mode="json", by_alias=True) for d in dsrs]
