"""Queued write handlers + precompute loaders for incidents."""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.incident_log_schema import IncidentLogCreate, IncidentLogUpdate
from services.incident_service import (
    add_incident,
    retrieve_incidents,
    retrieve_incidents_approaching_deadline,
    update_incident_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    qm = QueueManager.get_instance()
    for resource in ("incidents.list", "incidents.approaching_deadline"):
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": tenant_id, "resource": resource},
            )
        except Exception:
            logger.warning(
                "incident_writer: refresh enqueue failed tenant=%s resource=%s",
                tenant_id,
                resource,
                exc_info=True,
            )


@write_handler("incident.create")
async def _incident_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    log = IncidentLogCreate(**data)
    result = await add_incident(log_data=log, preassigned_id=resource_id)
    _enqueue_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "incident_type": result.incident_type,
    }


@write_handler("incident.update")
async def _incident_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = IncidentLogUpdate(**data)
    result = await update_incident_by_id(
        incident_id=resource_id, tenant_id=tenant_id, log_data=upd
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "status": result.status}


@register_precompute("incidents.list", scope=PrecomputeScope.TENANT)
async def _precompute_incidents_list(tenant_id: str) -> List[dict[str, Any]]:
    incidents = await retrieve_incidents(tenant_id=tenant_id, start=0, stop=100)
    return [i.model_dump(mode="json", by_alias=True) for i in incidents]


@register_precompute("incidents.approaching_deadline", scope=PrecomputeScope.TENANT)
async def _precompute_incidents_approaching_deadline(
    tenant_id: str,
) -> List[dict[str, Any]]:
    incidents = await retrieve_incidents_approaching_deadline(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [i.model_dump(mode="json", by_alias=True) for i in incidents]
