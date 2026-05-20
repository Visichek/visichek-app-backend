"""Queued write handlers + precompute loader for system users.

Every mutation invalidates the gate cache for the affected user so
permission changes / disables / MFA locks propagate faster than the
5-minute TTL.

Note: auth paths (login / refresh / signup / 2FA / logout) stay sync.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.bulk import run_bulk_handlers
from core.queue.gate_cache import invalidate_gate
from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.imports import AccountStatus
from schemas.system_user_schema import SystemUserCreate, SystemUserUpdate
from services.system_user_service import (
    add_system_user,
    admin_set_user_mfa,
    remove_system_user,
    retrieve_system_user_by_id,
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


@write_handler(
    "system_user.invite",
    invalidates=[
        "system_users.list",
        # actor_summary / host_summary / assigned_to_summary embedded across views
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
async def _system_user_invite(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    user = SystemUserCreate(**data)
    result = await add_system_user(user_data=user, preassigned_id=resource_id)
    _enqueue_list_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "email": result.email,
        "role": result.role.value if hasattr(result.role, "value") else result.role,
    }


@write_handler(
    "system_user.update",
    invalidates=[
        "system_users.list",
        # actor_summary / host_summary / assigned_to_summary embedded across views
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
async def _system_user_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = SystemUserUpdate(**data)
    result = await update_system_user_by_id(
        user_id=resource_id, tenant_id=tenant_id, user_data=upd
    )
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "email": result.email}


@write_handler(
    "system_user.delete",
    invalidates=[
        "system_users.list",
        # actor_summary / host_summary / assigned_to_summary embedded across views
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
async def _system_user_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", "") or ""
    await remove_system_user(user_id=resource_id, tenant_id=tenant_id)
    _invalidate_gate_roles(resource_id)
    _enqueue_list_refresh(tenant_id)
    return {"id": resource_id, "deleted": True}


@write_handler(
    "system_user.set_mfa",
    invalidates=[
        "system_users.list",
        # actor_summary / host_summary / assigned_to_summary embedded across views
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
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


@write_handler(
    "system_user.assign_department",
    invalidates=[
        "system_users.list",
        # actor_summary / host_summary / assigned_to_summary embedded across views
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
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


@write_handler(
    "system_user.bulk_delete",
    invalidates=[
        "system_users.list",
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
async def _system_user_bulk_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Bulk delete with super_admin protection and self-block.

    The route layer cannot enforce these on its own — it only sees the
    actor scope. Per-id checks happen here so the per-id row in
    `failed[]` carries a precise reason (`USER_DELETE_PROTECTED`).
    """
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_user_id = str(extras.get("actor_user_id") or "")
    tenant_scope = str(extras.get("tenant_scope") or "")

    from services.main_super_admin_guard import is_protected_main_super_admin

    async def _handle(user_id: str) -> dict[str, Any]:
        if user_id == actor_user_id:
            raise PermissionError("USER_DELETE_PROTECTED: cannot delete self")
        target = await retrieve_system_user_by_id(user_id=user_id)
        if not target:
            raise ValueError("User not found")
        if tenant_scope and target.tenant_id != tenant_scope:
            raise PermissionError("USER_DELETE_PROTECTED: cross-tenant blocked")
        if is_protected_main_super_admin(target):
            raise PermissionError("MAIN_SUPER_ADMIN_LOCKED")
        target_role = (
            target.role.value if hasattr(target.role, "value") else target.role
        )
        if target_role == "super_admin":
            raise PermissionError("USER_DELETE_PROTECTED: super_admin")
        await remove_system_user(user_id=user_id, tenant_id=target.tenant_id or "")
        invalidate_gate(user_id=user_id)
        return {"id": user_id, "deleted": True}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@write_handler(
    "system_user.bulk_deactivate",
    invalidates=[
        "system_users.list",
        "incidents.list",
        "appointments.list",
        "support_cases.list",
        "support_cases.admin_list",
    ],
)
async def _system_user_bulk_deactivate(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_user_id = str(extras.get("actor_user_id") or "")
    tenant_scope = str(extras.get("tenant_scope") or "")

    from services.main_super_admin_guard import is_protected_main_super_admin

    async def _handle(user_id: str) -> dict[str, Any]:
        if user_id == actor_user_id:
            raise PermissionError("USER_DEACTIVATE_PROTECTED: cannot deactivate self")
        target = await retrieve_system_user_by_id(user_id=user_id)
        if not target:
            raise ValueError("User not found")
        if tenant_scope and target.tenant_id != tenant_scope:
            raise PermissionError("USER_DEACTIVATE_PROTECTED: cross-tenant blocked")
        if is_protected_main_super_admin(target):
            raise PermissionError("MAIN_SUPER_ADMIN_LOCKED")
        upd = SystemUserUpdate(account_status=AccountStatus.INACTIVE)
        result = await update_system_user_by_id(
            user_id=user_id, tenant_id=target.tenant_id or "", user_data=upd
        )
        invalidate_gate(user_id=user_id)
        return {"id": result.id if result else user_id, "account_status": "INACTIVE"}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@write_handler(
    "system_user.bulk_reset_password",
    invalidates=["system_users.list"],
)
async def _system_user_bulk_reset_password(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Trigger password reset across users.

    Each target's password is generated server-side inside
    ``reset_system_user_password_by_authority`` and emailed to the
    user via the ``password_reset_temp`` template. The response
    intentionally never carries the cleartext — leaking it via the
    queue_job_log result would defeat the point of generating it
    server-side. The target's tokens are revoked and
    ``must_change_password`` is flipped to True so the next sign-in
    routes to the change-password screen.
    """
    from services.password_change_service import (
        reset_system_user_password_by_authority,
    )

    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_user_id = str(extras.get("actor_user_id") or "")
    tenant_scope = str(extras.get("tenant_scope") or "")

    async def _handle(user_id: str) -> dict[str, Any]:
        if user_id == actor_user_id:
            raise PermissionError("USE_SELF_PASSWORD_CHANGE_INSTEAD")
        await reset_system_user_password_by_authority(
            target_user_id=user_id,
            actor_id=actor_user_id,
            actor_role="super_admin",
            scope_tenant_id=tenant_scope,
        )
        invalidate_gate(user_id=user_id)
        # NOTE: deliberately NOT returning the plaintext password.
        return {"id": user_id, "password_reset": True}

    out = await run_bulk_handlers(ids, _handle, atomic=atomic)
    if tenant_scope:
        _enqueue_list_refresh(tenant_scope)
    return out


@register_precompute("system_users.list", scope=PrecomputeScope.TENANT)
async def _precompute_system_users_list(tenant_id: str) -> List[dict[str, Any]]:
    users = await retrieve_system_users(tenant_id=tenant_id, start=0, stop=100)
    return [u.model_dump(mode="json", by_alias=True) for u in users]
