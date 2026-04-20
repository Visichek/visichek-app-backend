"""Queued write handlers + precompute loaders for departments.

This module is the reference implementation of the new write / read
pipeline. Each ``@write_handler`` is called by the generic ``db.write``
dispatcher on the ``worker-writes`` service. Each ``@register_precompute``
loader is called by the ``worker-precompute`` service on behalf of
active tenants.

Routes (``api/v1/department_route.py``) never touch the database
directly for mutations — they call
:func:`core.queue.write_pipeline.enqueue_write` which routes the
payload here.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.department_schema import DepartmentCreate, DepartmentUpdate
from services.department_service import (
    add_department,
    remove_department,
    retrieve_departments_with_summary,
    update_department_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "departments.list"},
        )
    except Exception:
        logger.warning(
            "department_writer: precompute refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("department.create")
async def _department_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    created_by = data.pop("created_by", None)
    dept_data = DepartmentCreate(**data)
    result = await add_department(
        dept_data=dept_data,
        created_by=created_by,
        preassigned_id=resource_id,
    )
    _enqueue_list_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "code": result.code,
        "name": result.name,
    }


@write_handler("department.update")
async def _department_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = DepartmentUpdate(**data)
    result = await update_department_by_id(
        department_id=resource_id, tenant_id=tenant_id, dept_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "code": result.code, "name": result.name}


@write_handler("department.delete")
async def _department_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_department(department_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@register_precompute("departments.list", scope=PrecomputeScope.TENANT)
async def _precompute_departments_list(tenant_id: str) -> List[dict[str, Any]]:
    """Build the tenant's full first-page department list.

    Stored under ``precomputed:tenant:{tenant_id}:departments.list``.
    Paginated requests beyond the first page fall through to the live
    service because the hit rate beyond page 1 is rarely worth caching.
    """
    departments = await retrieve_departments_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [d.model_dump(mode="json", by_alias=True) for d in departments]
