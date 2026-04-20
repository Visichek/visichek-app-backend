"""Queued write handlers + precompute loader for tenants.

Tenants are app-admin managed; bootstrap (``/v1/admins/tenants/bootstrap``)
stays sync because it creates the first super_admin atomically and must
return an AppRollback path on failure. Plain CRUD POST/PATCH go through
the queue.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.tenant_schema import TenantCreate, TenantUpdate
from services.tenant_service import (
    add_tenant,
    retrieve_tenants_with_summary,
    update_tenant_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh() -> None:
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": "", "resource": "tenants.list"},
        )
    except Exception:
        logger.warning("tenant_writer: refresh enqueue failed", exc_info=True)


@write_handler("tenant.create")
async def _tenant_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant = TenantCreate(**data)
    result = await add_tenant(tenant_data=tenant, preassigned_id=resource_id)
    _enqueue_list_refresh()
    return {"id": result.id, "company_name": result.company_name}


@write_handler("tenant.update")
async def _tenant_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    upd = TenantUpdate(**data)
    result = await update_tenant_by_id(tenant_id=resource_id, tenant_data=upd)
    _enqueue_list_refresh()
    return {"id": result.id, "company_name": result.company_name}


@register_precompute("tenants.list", scope=PrecomputeScope.GLOBAL)
async def _precompute_tenants_list(_tenant_id: str) -> List[dict[str, Any]]:
    tenants = await retrieve_tenants_with_summary(start=0, stop=100)
    return [t.model_dump(mode="json", by_alias=True) for t in tenants]
