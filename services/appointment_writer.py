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
    retrieve_appointments_with_summary,
    update_appointment_by_id,
)

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


@write_handler("appointment.create", invalidates=[
        "appointments.list",
        # appointment_summary referenced from active visit lists / dashboard
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
    ])
async def _appointment_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    appt = AppointmentCreate(**data)
    result = await add_appointment(appt_data=appt, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
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
    tenant_id = data.pop("tenant_id", "") or ""
    upd = AppointmentUpdate(**data)
    result = await update_appointment_by_id(
        appointment_id=resource_id, tenant_id=tenant_id, appt_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "status": result.status}


@write_handler("appointment.delete", invalidates=[
        "appointments.list",
        # appointment_summary referenced from active visit lists / dashboard
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
    ])
async def _appointment_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_appointment(appointment_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@register_precompute("appointments.list", scope=PrecomputeScope.TENANT)
async def _precompute_appointments_list(tenant_id: str) -> List[dict[str, Any]]:
    appts = await retrieve_appointments_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [a.model_dump(mode="json", by_alias=True) for a in appts]
