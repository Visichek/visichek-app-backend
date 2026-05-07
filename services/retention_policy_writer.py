"""Queued write handlers + precompute loader for retention policies."""

from __future__ import annotations

import logging
from typing import Any, List

from bson import ObjectId

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from repositories.retention_policy_repo import (
    create_retention_policy,
    get_retention_policies,
    update_retention_policy,
)
from schemas.retention_policy_schema import (
    RetentionPolicyCreate,
    RetentionPolicyUpdate,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "retention_policies.list"},
        )
    except Exception:
        logger.warning(
            "retention_policy_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("retention_policy.create", invalidates=["retention_policies.list"])
async def _retention_policy_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    policy = RetentionPolicyCreate(**data)
    result = await create_retention_policy(policy, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id, "scope": result.scope}


@write_handler("retention_policy.update", invalidates=["retention_policies.list"])
async def _retention_policy_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = RetentionPolicyUpdate(**data)
    result = await update_retention_policy(
        {"_id": ObjectId(resource_id), "tenant_id": tenant_id}, upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id}


@register_precompute("retention_policies.list", scope=PrecomputeScope.TENANT)
async def _precompute_retention_policies_list(tenant_id: str) -> List[dict[str, Any]]:
    policies = await get_retention_policies({"tenant_id": tenant_id})
    return [p.model_dump(mode="json", by_alias=True) for p in policies]
