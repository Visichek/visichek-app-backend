"""Queued write handler + precompute loader for tenant settings.

Tenant settings is upsert-style — one record per tenant keyed by
``tenant_id`` — so the writer's ``resource_id`` is always the tenant_id.
The same writer backs both ``/v1/tenants/{id}/settings`` and the
unified ``/v1/tenant-settings`` endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.tenant_settings_schema import TenantSettingsUpdate
from services.tenant_settings_service import (
    retrieve_or_create_tenant_settings,
    update_tenant_settings_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "tenant.settings"},
        )
    except Exception:
        logger.warning(
            "tenant_settings_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("tenant_settings.update")
async def _tenant_settings_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", resource_id) or resource_id
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    upd = TenantSettingsUpdate(**data)
    result = await update_tenant_settings_by_id(
        tenant_id=tenant_id,
        data=upd,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id}


@register_precompute("tenant.settings", scope=PrecomputeScope.TENANT)
async def _precompute_tenant_settings(tenant_id: str) -> Optional[dict[str, Any]]:
    if not tenant_id:
        return None
    result = await retrieve_or_create_tenant_settings(tenant_id)
    return result.model_dump(mode="json", by_alias=True)
