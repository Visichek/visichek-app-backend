"""Queued write handler + precompute loaders for compliance endpoints."""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from repositories.data_processing_register_repo import (
    create_dpr_entry,
    get_dpr_entries,
)
from repositories.deletion_log_repo import get_deletion_logs
from schemas.data_processing_register_schema import DPRCreate

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "compliance.register"},
        )
    except Exception:
        logger.warning(
            "compliance_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("compliance.register_dpr")
async def _compliance_register_dpr(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    entry = DPRCreate(**data)
    result = await create_dpr_entry(entry, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id}


@register_precompute("compliance.register", scope=PrecomputeScope.TENANT)
async def _precompute_compliance_register(tenant_id: str) -> List[dict[str, Any]]:
    entries = await get_dpr_entries({"tenant_id": tenant_id})
    return [e.model_dump(mode="json", by_alias=True) for e in entries]


@register_precompute("compliance.deletion_logs", scope=PrecomputeScope.TENANT)
async def _precompute_compliance_deletion_logs(tenant_id: str) -> List[Any]:
    logs = await get_deletion_logs({"tenant_id": tenant_id}, start=0, stop=100)
    return [
        log.model_dump(mode="json", by_alias=True) if hasattr(log, "model_dump") else log
        for log in logs
    ]
