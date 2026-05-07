"""Queued write handlers + precompute loader for appointments."""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.appointment_schema import AppointmentCreate, AppointmentUpdate
from services.appointment_service import (
    add_appointment,
    remove_appointment,
    retrieve_appointment_by_id,
    retrieve_appointments_with_summary,
    update_appointment_by_id_with_diff,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "appointments.list"},
        )
    except Exception:
        logger.warning(
            "appointment_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _pop_actor(data: dict[str, Any]) -> tuple[str, str, str | None]:
    """Strip actor metadata out of the writer payload.

    Routes thread ``actor_id`` / ``actor_role`` / ``request_id`` into the
    payload so write handlers can record audit events without re-fetching
    the queue_job_log row.
    """
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    request_id = data.pop("_request_id", None)
    return actor_id, actor_role, request_id


@write_handler("appointment.create", invalidates=[
        "appointments.list",
        # appointment_summary referenced from active visit lists / dashboard
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
    ])
async def _appointment_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    appt = AppointmentCreate(**data)
    result = await add_appointment(appt_data=appt, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="appointment.created",
            resource_type="appointment",
            resource_id=result.id or resource_id,
            tenant_id=result.tenant_id,
            details={
                "host_id": result.host_id,
                "department_id": result.department_id,
                "visitor_profile_id": result.visitor_profile_id,
                "scheduled_datetime": result.scheduled_datetime,
            },
            request_id=request_id,
        )
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "scheduled_datetime": result.scheduled_datetime,
    }


@write_handler("appointment.update", invalidates=[
        "appointments.list",
        # appointment_summary referenced from active visit lists / dashboard
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
    ])
async def _appointment_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.pop("tenant_id", "") or ""
    upd = AppointmentUpdate(**data)
    _before, after, changes = await update_appointment_by_id_with_diff(
        appointment_id=resource_id, tenant_id=tenant_id, appt_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="appointment.updated",
            resource_type="appointment",
            resource_id=after.id or resource_id,
            tenant_id=tenant_id,
            details={"changes": changes} if changes else {"changes": {}},
            request_id=request_id,
        )
    return {"id": after.id, "status": after.status, "changed_fields": list(changes)}


@write_handler("appointment.delete", invalidates=[
        "appointments.list",
        # appointment_summary referenced from active visit lists / dashboard
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
    ])
async def _appointment_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.get("tenant_id", "") or ""
    snapshot = None
    try:
        snapshot = await retrieve_appointment_by_id(
            appointment_id=resource_id, tenant_id=tenant_id
        )
    except Exception:
        # 404 / invalid id paths still need to surface from remove_appointment;
        # snapshot is best-effort context for the audit row.
        snapshot = None
    await remove_appointment(appointment_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        details: dict[str, Any] = {}
        if snapshot is not None:
            details = {
                "host_id": snapshot.host_id,
                "department_id": snapshot.department_id,
                "scheduled_datetime": snapshot.scheduled_datetime,
                "status": getattr(snapshot.status, "value", snapshot.status),
            }
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="appointment.deleted",
            resource_type="appointment",
            resource_id=resource_id,
            tenant_id=tenant_id,
            details=details,
            request_id=request_id,
        )
    return {"id": resource_id, "deleted": True}


@register_precompute("appointments.list", scope=PrecomputeScope.TENANT)
async def _precompute_appointments_list(tenant_id: str) -> List[dict[str, Any]]:
    appts = await retrieve_appointments_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [a.model_dump(mode="json", by_alias=True) for a in appts]
