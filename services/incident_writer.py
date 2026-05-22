"""Queued write handlers + precompute loaders for incidents."""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
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


async def _nudge_dashboard(tenant_id: str) -> None:
    """Best-effort live-dashboard nudge (tenant Insights + platform admin)."""
    try:
        from services.dashboard_stream_service import publish_dashboard_refresh

        await publish_dashboard_refresh(tenant_id)
    except Exception:
        logger.debug("incident_writer: dashboard nudge failed", exc_info=True)


@write_handler(
    "incident.create",
    invalidates=[
        "incidents.list",
        "incidents.approaching_deadline",
        # status / counts also reflected on tenant dashboard rollups
        "dashboard.stats",
    ],
)
async def _incident_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    log = IncidentLogCreate(**data)
    result = await add_incident(log_data=log, preassigned_id=resource_id)
    _enqueue_refresh(result.tenant_id)
    await _nudge_dashboard(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "incident_type": result.incident_type,
    }


@write_handler(
    "incident.update",
    invalidates=[
        "incidents.list",
        "incidents.approaching_deadline",
        # status / counts also reflected on tenant dashboard rollups
        "dashboard.stats",
    ],
)
async def _incident_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = IncidentLogUpdate(**data)
    result = await update_incident_by_id(
        incident_id=resource_id, tenant_id=tenant_id, log_data=upd
    )
    _enqueue_refresh(tenant_id)
    await _nudge_dashboard(tenant_id)
    return {"id": result.id, "status": result.status}


@write_handler(
    "incident.bulk_mark_notified",
    invalidates=["incidents.list", "incidents.approaching_deadline", "dashboard.stats"],
)
async def _incident_bulk_mark_notified(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    notification_sent_at = int(extras.get("notification_sent_at") or 0)

    async def _handle(incident_id: str) -> dict[str, Any]:
        upd_payload: dict[str, Any] = {"ndpc_notified": True}
        if notification_sent_at:
            upd_payload["notification_sent_at"] = notification_sent_at
        upd = IncidentLogUpdate(**upd_payload)
        result = await update_incident_by_id(
            incident_id=incident_id, tenant_id=tenant_scope, log_data=upd
        )
        return {"id": result.id if result else incident_id, "ndpc_notified": True}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_refresh(tenant_scope)
    return out


@write_handler(
    "incident.bulk_status",
    invalidates=["incidents.list", "incidents.approaching_deadline", "dashboard.stats"],
)
async def _incident_bulk_status(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    new_status = str(extras.get("status") or "")

    async def _handle(incident_id: str) -> dict[str, Any]:
        upd = IncidentLogUpdate(status=new_status)  # type: ignore[arg-type]
        result = await update_incident_by_id(
            incident_id=incident_id, tenant_id=tenant_scope, log_data=upd
        )
        return {"id": result.id if result else incident_id, "status": new_status}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_refresh(tenant_scope)
    return out


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
