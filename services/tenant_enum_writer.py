"""Queued write handlers + precompute loader for tenant enums.

Tenant enums are upsert-style — one row per ``(tenant_id, kind)`` —
so the writer's ``resource_id`` is always the row's MongoDB id, but the
caller really wants to address the resource by ``(tenant_id, kind)``.
We carry the kind through ``data`` rather than the resource_id so the
worker can resolve it the same way the route does.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.imports import TenantEnumKind
from schemas.tenant_enum_schema import TenantEnumUpdate
from services.tenant_enum_service import (
    list_enums_for_tenant,
    reset_enum_to_defaults,
    update_enum_options,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "tenant_enums.list"},
        )
    except Exception:
        logger.warning(
            "tenant_enum_writer: precompute refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("tenant_enum.update", invalidates=["tenant_enums.list"])
async def _tenant_enum_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    kind_value = data.pop("kind", "") or ""
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    request_id = data.pop("_request_id", None)
    kind = TenantEnumKind(kind_value)
    upd = TenantEnumUpdate(**data)
    result = await update_enum_options(
        tenant_id=tenant_id,
        kind=kind,
        data=upd,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "tenant_id": tenant_id, "kind": kind.value}


@write_handler("tenant_enum.reset", invalidates=["tenant_enums.list"])
async def _tenant_enum_reset(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    kind_value = data.pop("kind", "") or ""
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    request_id = data.pop("_request_id", None)
    kind = TenantEnumKind(kind_value)
    result = await reset_enum_to_defaults(
        tenant_id=tenant_id,
        kind=kind,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "tenant_id": tenant_id, "kind": kind.value}


@register_precompute("tenant_enums.list", scope=PrecomputeScope.TENANT)
async def _precompute_tenant_enums_list(tenant_id: str) -> List[dict[str, Any]]:
    rows = await list_enums_for_tenant(tenant_id)
    return [r.model_dump(mode="json", by_alias=True) for r in rows]
