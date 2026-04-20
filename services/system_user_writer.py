"""Queued write handlers + precompute loader for system users.

Every mutation invalidates the gate cache for the affected user so
permission changes / disables / MFA locks propagate faster than the
5-minute TTL.

Note: auth paths (login / refresh / signup / 2FA / logout) stay sync.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.gate_cache import invalidate_gate
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.system_user_schema import SystemUserCreate, SystemUserUpdate
from services.system_user_service import (
    add_system_user,
    admin_set_user_mfa,
    remove_system_user,
    retrieve_system_users,
    update_system_user_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "system_users.list"},
        )
    except Exception:
        logger.warning(
            "system_user_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _invalidate_gate_roles(user_id: str) -> None:
    """Drop every role-specific gate snapshot for this user."""
    if not user_id:
        return
    try:
        invalidate_gate(user_id=user_id)
    except Exception:
        logger.warning(
            "system_user_writer: gate invalidate failed user=%s",
            user_id,
            exc_info=True,
        )


@write_handler("system_user.invite")
async def _system_user_invite(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user = SystemUserCreate(**data)
    result = await add_system_user(user_data=user, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "email": result.email,
        "role": result.role.value
        if hasattr(result.role, "value")
        else result.role,
    }


@write_handler("system_user.update")
async def _system_user_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = SystemUserUpdate(**data)
    result = await update_system_user_by_id(
        user_id=resource_id, tenant_id=tenant_id, user_data=upd
    )
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "email": result.email}


@write_handler("system_user.delete")
async def _system_user_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_system_user(user_id=resource_id, tenant_id=tenant_id)
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@write_handler("system_user.set_mfa")
async def _system_user_set_mfa(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    mfa_enabled = bool(data.get("mfa_enabled", False))
    mfa_locked_by_admin = bool(data.get("mfa_locked_by_admin", False))
    result = await admin_set_user_mfa(
        user_id=resource_id,
        tenant_id=tenant_id,
        mfa_enabled=mfa_enabled,
        mfa_locked_by_admin=mfa_locked_by_admin,
    )
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "mfa_enabled": result.mfa_enabled}


@write_handler("system_user.assign_department")
async def _system_user_assign_department(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    department_id = data.get("department_id")
    upd = SystemUserUpdate(department_id=department_id)
    result = await update_system_user_by_id(
        user_id=resource_id, tenant_id=tenant_id, user_data=upd
    )
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "department_id": result.department_id}


@register_precompute("system_users.list", scope=PrecomputeScope.TENANT)
async def _precompute_system_users_list(tenant_id: str) -> List[dict[str, Any]]:
    users = await retrieve_system_users(tenant_id=tenant_id, start=0, stop=100)
    return [u.model_dump(mode="json", by_alias=True) for u in users]
