"""Queued write handlers + precompute loader for hosts.

Routes (``api/v1/host_route.py``) never touch the database directly for
mutations — they call :func:`core.queue.write_pipeline.enqueue_write`
which routes the payload here, onto the ``worker-writes`` service.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.host_schema import HostCreate, HostUpdate
from services.audit_service import record_audit_event
from services.host_service import (
    add_host,
    remove_host,
    retrieve_host_by_id,
    retrieve_hosts_with_summary,
    update_host_by_id_with_diff,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "hosts.list"},
        )
    except Exception:
        logger.warning(
            "host_writer: precompute refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _pop_actor(data: dict[str, Any]) -> tuple[str, str, str | None]:
    """Strip actor metadata out of the writer payload."""
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    request_id = data.pop("_request_id", None)
    return actor_id, actor_role, request_id


@write_handler("host.create", invalidates=["hosts.list"])
async def _host_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    host = HostCreate(**data)
    result = await add_host(host_data=host, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="host.created",
            resource_type="host",
            resource_id=result.id or resource_id,
            tenant_id=result.tenant_id,
            details={
                "name": result.name,
                "department_id": result.department_id,
                "source_system_user_id": result.source_system_user_id,
            },
            request_id=request_id,
        )
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "name": result.name,
    }


@write_handler("host.update", invalidates=["hosts.list"])
async def _host_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.pop("tenant_id", "") or ""
    upd = HostUpdate(**data)
    _before, after, changes = await update_host_by_id_with_diff(
        host_id=resource_id, tenant_id=tenant_id, host_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="host.updated",
            resource_type="host",
            resource_id=after.id or resource_id,
            tenant_id=tenant_id,
            details={"changes": changes},
            request_id=request_id,
        )
    return {"id": after.id, "changed_fields": list(changes)}


@write_handler("host.delete", invalidates=["hosts.list"])
async def _host_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.get("tenant_id", "") or ""
    snapshot = None
    try:
        snapshot = await retrieve_host_by_id(
            host_id=resource_id, tenant_id=tenant_id
        )
    except Exception:
        # 404 / invalid id paths still surface from remove_host; the
        # snapshot is best-effort context for the audit row.
        snapshot = None
    await remove_host(host_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        details: dict[str, Any] = {}
        if snapshot is not None:
            details = {
                "name": snapshot.name,
                "department_id": snapshot.department_id,
                "source_system_user_id": snapshot.source_system_user_id,
            }
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="host.deleted",
            resource_type="host",
            resource_id=resource_id,
            tenant_id=tenant_id,
            details=details,
            request_id=request_id,
        )
    return {"id": resource_id, "deleted": True}


@write_handler("host.bulk_delete", invalidates=["hosts.list"])
async def _host_bulk_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")

    async def _handle(host_id: str) -> dict[str, Any]:
        await remove_host(host_id=host_id, tenant_id=tenant_scope)
        return {"id": host_id, "deleted": True}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@register_precompute("hosts.list", scope=PrecomputeScope.TENANT)
async def _precompute_hosts_list(tenant_id: str) -> List[dict[str, Any]]:
    hosts = await retrieve_hosts_with_summary(tenant_id=tenant_id, start=0, stop=100)
    return [h.model_dump(mode="json", by_alias=True) for h in hosts]
