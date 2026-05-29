"""Queued write handlers + precompute loader for DSRs."""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
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


@write_handler("dsr.create", invalidates=["dsr.list"])
async def _dsr_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    dsr = DSRCreate(**data)
    result = await add_dsr(dsr_data=dsr, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)

    # Fan out notifications. The DSR is on a legal SLA clock: notify the
    # tenant's privacy officers AND every platform admin so the SaaS
    # operator has cross-tenant compliance visibility. Best-effort —
    # notification failures never roll back the write.
    try:
        from services.notification_service import (
            notify_dsr_submitted_tenant_dpos,
            notify_dsr_submitted_to_platform_admins,
        )

        tenant_name: str | None = None
        try:
            from services.tenant_service import retrieve_tenant_by_id

            tenant = await retrieve_tenant_by_id(result.tenant_id)
            tenant_name = getattr(tenant, "company_name", None)
        except Exception:
            logger.warning(
                "dsr_writer: tenant lookup for notification label failed tenant=%s",
                result.tenant_id,
                exc_info=True,
            )

        await notify_dsr_submitted_tenant_dpos(
            tenant_id=result.tenant_id, dsr_id=result.id or resource_id
        )
        await notify_dsr_submitted_to_platform_admins(
            tenant_id=result.tenant_id,
            dsr_id=result.id or resource_id,
            tenant_name=tenant_name,
        )
    except Exception:
        logger.warning(
            "dsr_writer: notification fan-out failed dsr=%s",
            result.id,
            exc_info=True,
        )

    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "request_type": result.request_type,
        "status": result.status,
    }


@write_handler("dsr.update", invalidates=["dsr.list"])
async def _dsr_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = DSRUpdate(**data)
    result = await update_dsr_by_id(
        dsr_id=resource_id, tenant_id=tenant_id, dsr_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "status": result.status}


@write_handler("dsr.bulk_acknowledge", invalidates=["dsr.list"])
async def _dsr_bulk_acknowledge(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")

    async def _handle(dsr_id: str) -> dict[str, Any]:
        upd = DSRUpdate(status="in_progress")  # type: ignore[arg-type]
        result = await update_dsr_by_id(
            dsr_id=dsr_id, tenant_id=tenant_scope, dsr_data=upd
        )
        return {"id": result.id if result else dsr_id, "status": "in_progress"}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@write_handler("dsr.bulk_reject", invalidates=["dsr.list"])
async def _dsr_bulk_reject(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    reason = str(extras.get("reason") or "")[:2000]

    async def _handle(dsr_id: str) -> dict[str, Any]:
        upd_payload: dict[str, Any] = {"status": "rejected"}
        if reason:
            upd_payload["rejection_reason"] = reason
        upd = DSRUpdate(**upd_payload)
        result = await update_dsr_by_id(
            dsr_id=dsr_id, tenant_id=tenant_scope, dsr_data=upd
        )
        return {"id": result.id if result else dsr_id, "status": "rejected"}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@register_precompute("dsr.list", scope=PrecomputeScope.TENANT)
async def _precompute_dsr_list(tenant_id: str) -> List[dict[str, Any]]:
    dsrs = await retrieve_dsrs(tenant_id=tenant_id, start=0, stop=100)
    return [d.model_dump(mode="json", by_alias=True) for d in dsrs]
