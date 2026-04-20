"""Queued write handler + precompute loader for visitor profiles.

Note: creation happens internally via ``get_or_create_visitor_profile``
during check-in flows; only the update path is exposed to clients and
queued here.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.visitor_profile_schema import VisitorProfileUpdate
from services.visitor_profile_service import (
    retrieve_visitor_profiles_with_summary,
    update_profile_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "visitor_profiles.list"},
        )
    except Exception:
        logger.warning(
            "visitor_profile_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("visitor_profile.update")
async def _visitor_profile_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = VisitorProfileUpdate(**data)
    result = await update_profile_by_id(
        profile_id=resource_id, tenant_id=tenant_id, profile_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "full_name": result.full_name}


@register_precompute("visitor_profiles.list", scope=PrecomputeScope.TENANT)
async def _precompute_visitor_profiles_list(tenant_id: str) -> List[dict[str, Any]]:
    profiles = await retrieve_visitor_profiles_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [p.model_dump(mode="json", by_alias=True) for p in profiles]
