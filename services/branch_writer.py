"""Queued write handlers + precompute loader for branches.

Branch writers preserve the existing invariants:
- max_branches cap (enforced inside ``add_branch``)
- last-branch protection for deactivate / delete
- duplicate-name check per tenant
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.branch_schema import BranchCreate, BranchUpdate
from services.branch_service import (
    add_branch,
    deactivate_branch,
    remove_branch,
    retrieve_branches_for_tenant,
    update_branch_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "branches.list"},
        )
    except Exception:
        logger.warning(
            "branch_writer: refresh enqueue failed tenant=%s", tenant_id, exc_info=True
        )


@write_handler("branch.create", invalidates=[
        "branches.list",
        # Embedded as branch_summary on system_user / department / appointment lists
        "system_users.list",
        "departments.list",
        "appointments.list",
    ])
async def _branch_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    branch = BranchCreate(**data)
    result = await add_branch(branch_data=branch, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id, "name": result.name}


@write_handler("branch.update", invalidates=[
        "branches.list",
        # Embedded as branch_summary on system_user / department / appointment lists
        "system_users.list",
        "departments.list",
        "appointments.list",
    ])
async def _branch_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = BranchUpdate(**data)
    result = await update_branch_by_id(branch_id=resource_id, update_data=upd)
    _enqueue_list_refresh(tenant_id)
    if result is None:
        return {"id": resource_id, "updated": False}
    return {"id": result.id, "name": result.name}


@write_handler("branch.deactivate", invalidates=[
        "branches.list",
        # Embedded as branch_summary on system_user / department / appointment lists
        "system_users.list",
        "departments.list",
        "appointments.list",
    ])
async def _branch_deactivate(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    result = await deactivate_branch(branch_id=resource_id)
    _enqueue_list_refresh(tenant_id)
    if result is None:
        return {"id": resource_id, "deactivated": False}
    return {"id": result.id, "status": result.status}


@write_handler("branch.delete", invalidates=[
        "branches.list",
        # Embedded as branch_summary on system_user / department / appointment lists
        "system_users.list",
        "departments.list",
        "appointments.list",
    ])
async def _branch_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_branch(branch_id=resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@register_precompute("branches.list", scope=PrecomputeScope.TENANT)
async def _precompute_branches_list(tenant_id: str) -> List[dict[str, Any]]:
    branches = await retrieve_branches_for_tenant(tenant_id, start=0, stop=100)
    return [b.model_dump(mode="json", by_alias=True) for b in branches]
