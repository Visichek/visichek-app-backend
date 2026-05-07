"""Queued write handlers + precompute loader for check-in configurations.

Note: ``POST /{config_id}/checkins`` (public visitor submission) stays
synchronous — that endpoint needs immediate response for the kiosk UX.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.checkin_config_schema import CheckinConfigCreate, CheckinConfigUpdate
from services.checkin_config_service import (
    create_config,
    list_configs_for_tenant,
    update_config,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "checkin_configs.list"},
        )
    except Exception:
        logger.warning(
            "checkin_config_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("checkin_config.create", invalidates=["checkin_configs.list"])
async def _checkin_config_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    payload = CheckinConfigCreate(**data)
    result = await create_config(payload=payload, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id}


@write_handler("checkin_config.update", invalidates=["checkin_configs.list"])
async def _checkin_config_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = CheckinConfigUpdate(**data)
    result = await update_config(resource_id, upd)
    _enqueue_list_refresh(tenant_id or result.tenant_id)
    return {"id": result.id}


@register_precompute("checkin_configs.list", scope=PrecomputeScope.TENANT)
async def _precompute_checkin_configs_list(tenant_id: str) -> List[dict[str, Any]]:
    configs, _ = await list_configs_for_tenant(tenant_id=tenant_id, skip=0, limit=100)
    return [c.model_dump(mode="json", by_alias=True) for c in configs]
