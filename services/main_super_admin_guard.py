"""Guards that protect the main super_admin row from being mutated.

Three invariants enforced here:

   I-1  Role of the main super_admin cannot change.
   I-2  account_status of the main super_admin cannot move away from
        ACTIVE, and is_active cannot move to False — i.e. nothing can
        disable the main super_admin via the normal update path.
   I-3  The main super_admin row cannot be hard-deleted via the
        delete path.

The legitimate way to move ownership is the
``/v1/system-users/transfer-main-super-admin`` two-step MFA flow.
Tenant offboarding (``services/tenant_offboarding_service.py``) is the
only legitimate way to deactivate the main super_admin without a
transfer — it sets ``allow_main_super_admin=True`` on the bypass guard
so the offboarding sweep is not blocked.

An ENV escape hatch (``ENFORCE_MAIN_SUPER_ADMIN=false``) disables the
guards entirely so a production incident never requires a code revert.
Default is ``true``.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from schemas.imports import AccountStatus
from schemas.system_user_schema import SystemUserOut, SystemUserUpdate


def _enforcement_enabled() -> bool:
    raw = (os.getenv("ENFORCE_MAIN_SUPER_ADMIN") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _is_protected(existing: SystemUserOut) -> bool:
    return bool(getattr(existing, "is_main_super_admin", False))


def _raise_locked(operation: str, extra: dict[str, Any] | None = None) -> None:
    detail: dict[str, Any] = {
        "message": (
            "This row is the tenant's main super admin and cannot be "
            f"{operation} directly. Transfer the role first via "
            "POST /v1/system-users/transfer-main-super-admin/initiate."
        ),
        "code": "MAIN_SUPER_ADMIN_LOCKED",
    }
    if extra:
        detail.update(extra)
    raise HTTPException(status_code=403, detail=detail)


def guard_system_user_update(
    *,
    existing: SystemUserOut,
    update: SystemUserUpdate,
) -> None:
    """Block role / status / activation changes on the main super_admin row.

    Called from ``update_system_user_by_id`` BEFORE the repo write. The
    update is rejected if ANY of the following is true on a protected row:

       - ``update.role`` is set AND differs from the existing role.
       - ``update.account_status`` is set AND is not ACTIVE.
       - ``update.is_active`` is set AND is False.

    Pure changes (e.g. updating full_name or branch_ids) are allowed —
    we only protect the fields that define "is the main owner still
    here and still in charge".
    """
    if not _enforcement_enabled() or not _is_protected(existing):
        return

    touched: list[str] = []

    if update.role is not None:
        new_role = update.role.value if hasattr(update.role, "value") else str(update.role)
        old_role = existing.role.value if hasattr(existing.role, "value") else str(existing.role)
        if new_role != old_role:
            touched.append("role")

    if update.account_status is not None:
        new_status = (
            update.account_status.value
            if hasattr(update.account_status, "value")
            else str(update.account_status)
        )
        if new_status != AccountStatus.ACTIVE.value:
            touched.append("account_status")

    if update.is_active is not None and update.is_active is False:
        touched.append("is_active")

    if touched:
        _raise_locked(
            "modified",
            extra={"locked_fields": touched, "user_id": existing.id},
        )


def guard_system_user_delete(*, existing: SystemUserOut) -> None:
    """Block hard-delete of the main super_admin row."""
    if not _enforcement_enabled() or not _is_protected(existing):
        return
    _raise_locked("deleted", extra={"user_id": existing.id})


def is_protected_main_super_admin(user: SystemUserOut | None) -> bool:
    """Public predicate the bulk writers use to skip protected rows
    without raising — they record a per-id failure with
    ``MAIN_SUPER_ADMIN_LOCKED`` instead.
    """
    if not _enforcement_enabled():
        return False
    if user is None:
        return False
    return _is_protected(user)
