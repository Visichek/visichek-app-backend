"""Main super_admin invariant — backfill + periodic self-heal.

Runs in two places:

   1. ``main.lifespan`` — once at app boot, AFTER index creation. Ensures
      pre-existing tenants (from before the invariant landed) end up with
      exactly one ``is_main_super_admin=True`` row each.
   2. ``main.lifespan`` APScheduler job — every 6 hours, same function.
      Catches drift (e.g. someone manually flipped flags out-of-band).

Rules per tenant:

   - Look only at ACTIVE super_admins. INACTIVE / SUSPENDED rows can't
     own the tenant, so they're never the main.
   - Order by ``date_created`` ASC. The first row wins.
   - Clear ``is_main_super_admin=True`` from every other row in the
     tenant first (so the partial-unique index can never see two True
     rows simultaneously). Then $set True on the winner.
   - If the winner already has True, no write.

Failure modes the check classifies but does NOT auto-fix:

   - Tenant has zero ACTIVE super_admins (the tenant is orphaned —
     someone needs to recover it via
     ``POST /v1/admins/tenants/{tenant_id}/super-admins``). The check
     OPENS A HIGH-PRIORITY SUPPORT CASE for the tenant so platform
     on-call sees it within the SLA window.
   - Tenant is itself inactive (``tenant_companies.is_active=False``).
     Skipped — the offboarding flow is the expected state.

Every flag flip emits a ``tenant.main_super_admin_assigned`` audit
event. The auto-opened support case emits its own audit via the
support-case service. Idempotent: re-running on a clean DB does
zero writes and opens no new cases.
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from core.database import db
from repositories.system_user_repo import (
    clear_main_super_admin_flag_for_tenant,
    list_active_super_admins_oldest_first,
    set_main_super_admin_flag,
)
from schemas.imports import (
    SupportCaseCategory,
    SupportCasePriority,
    SupportCaseStatus,
)
from schemas.support_case_schema import SupportCaseCreate
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


_NO_SUPER_ADMIN_CASE_SUBJECT = "Tenant has no active super_admin"


async def _record_assigned(
    *,
    tenant_id: str,
    new_main_user_id: str,
    reason: str,
) -> None:
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="tenant.main_super_admin_assigned",
            resource_type="system_user",
            resource_id=new_main_user_id,
            tenant_id=tenant_id,
            details={
                "reason": reason,
                "to_user_id": new_main_user_id,
            },
        )
    except Exception:
        logger.warning(
            "audit for main super_admin auto-assign failed (tenant=%s)",
            tenant_id,
            exc_info=True,
        )


async def _record_invariant_warning(
    *,
    tenant_id: str,
    issue: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"issue": issue}
    if details:
        payload.update(details)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="tenant.main_super_admin_invariant_warning",
            resource_type="tenant",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            details=payload,
        )
    except Exception:
        logger.warning(
            "audit for main super_admin invariant warning failed (tenant=%s)",
            tenant_id,
            exc_info=True,
        )


async def _open_no_super_admin_support_case(tenant_id: str) -> None:
    """Open a high-priority platform support case for an orphaned tenant.

    Idempotent: an open case with the same subject is reused if it
    already exists, so the periodic check doesn't spam a new case on
    every run.
    """
    try:
        from schemas.support_case_schema import OPEN_STATUSES

        existing = await db.support_cases.find_one(
            {
                "tenant_id": tenant_id,
                "subject": _NO_SUPER_ADMIN_CASE_SUBJECT,
                "status": {"$in": list(OPEN_STATUSES)},
            }
        )
        if existing:
            return

        case = SupportCaseCreate(
            tenant_id=tenant_id,
            opened_by="system",
            opened_by_role="admin",
            subject=_NO_SUPER_ADMIN_CASE_SUBJECT,
            description=(
                "The main super_admin invariant check found this tenant has zero "
                "ACTIVE super_admins. No one owns the account: billing changes, "
                "user invites, and policy updates are all blocked from the "
                "tenant side until a super_admin is restored.\n\n"
                "Recovery: POST /v1/admins/tenants/{tenant_id}/super-admins "
                "(application admin only) to add a new super_admin. The new "
                "row will automatically receive the is_main_super_admin flag "
                "because no other super_admin exists for the tenant.\n\n"
                "Auto-opened by services/main_super_admin_backfill.py. This "
                "case is re-used on every run while the tenant remains in "
                "this state, so it will not duplicate."
            ),
            category=SupportCaseCategory.ACCOUNT,
            priority=SupportCasePriority.HIGH,
            status=SupportCaseStatus.OPEN,
        )
        await db.support_cases.insert_one(case.model_dump())
        logger.warning(
            "main_super_admin invariant: opened HIGH-priority support case "
            "for tenant=%s (no active super_admin)",
            tenant_id,
        )
    except Exception:
        logger.warning(
            "auto-open support case for orphaned tenant failed (tenant=%s)",
            tenant_id,
            exc_info=True,
        )


async def _heal_one_tenant(tenant_id: str) -> dict[str, str]:
    """Bring one tenant's main_super_admin state into compliance.

    Returns one of:
       {"outcome": "no_op"}
       {"outcome": "assigned", "user_id": "..."}
       {"outcome": "duplicates_collapsed", "user_id": "..."}
       {"outcome": "warning_no_active_super_admin"}
    """
    active = await list_active_super_admins_oldest_first(tenant_id)
    if not active:
        await _record_invariant_warning(
            tenant_id=tenant_id, issue="no_active_super_admin"
        )
        await _open_no_super_admin_support_case(tenant_id)
        return {"outcome": "warning_no_active_super_admin"}

    earliest = active[0]
    earliest_id = earliest.id or ""

    flagged = [u for u in active if getattr(u, "is_main_super_admin", False)]

    if len(flagged) == 1 and flagged[0].id == earliest_id:
        # Already in a valid state.
        return {"outcome": "no_op"}

    # Either zero rows flagged, or wrong row(s) flagged, or multiple
    # flagged. Clear everything except the earliest, then set the
    # earliest. Order matters for the partial-unique index — we never
    # leave two True rows in a tenant simultaneously.
    cleared = await clear_main_super_admin_flag_for_tenant(
        tenant_id, except_user_id=earliest_id
    )
    if not earliest.is_main_super_admin:
        await set_main_super_admin_flag(
            user_id=earliest_id, tenant_id=tenant_id, value=True
        )

    if cleared > 0 and len(flagged) > 1:
        outcome = "duplicates_collapsed"
    else:
        outcome = "assigned"

    await _record_assigned(
        tenant_id=tenant_id,
        new_main_user_id=earliest_id,
        reason="backfill" if outcome == "assigned" else "duplicate_collapse",
    )
    return {"outcome": outcome, "user_id": earliest_id}


async def ensure_main_super_admin_invariant() -> dict[str, Any]:
    """Scan every active tenant; ensure exactly one main super_admin each.

    Returns a summary dict suitable for logging at INFO. Errors on
    individual tenants are caught and surfaced in ``per_tenant_errors``
    so the loop never crashes mid-scan.
    """
    summary: dict[str, Any] = {
        "tenants_scanned": 0,
        "main_assigned": 0,
        "duplicates_collapsed": 0,
        "no_op": 0,
        "warnings_no_active_super_admin": 0,
        "skipped_inactive_tenants": 0,
        "per_tenant_errors": 0,
    }

    try:
        cursor = db.tenant_companies.find({}, {"_id": 1, "is_active": 1})
        async for tenant_doc in cursor:
            summary["tenants_scanned"] += 1
            tenant_id_obj = tenant_doc.get("_id")
            tenant_id = str(tenant_id_obj) if isinstance(tenant_id_obj, ObjectId) else str(tenant_id_obj)
            if tenant_doc.get("is_active") is False:
                summary["skipped_inactive_tenants"] += 1
                continue
            try:
                result = await _heal_one_tenant(tenant_id)
                outcome = result.get("outcome")
                if outcome == "assigned":
                    summary["main_assigned"] += 1
                elif outcome == "duplicates_collapsed":
                    summary["duplicates_collapsed"] += 1
                elif outcome == "no_op":
                    summary["no_op"] += 1
                elif outcome == "warning_no_active_super_admin":
                    summary["warnings_no_active_super_admin"] += 1
            except Exception:
                summary["per_tenant_errors"] += 1
                logger.warning(
                    "main_super_admin invariant: per-tenant heal failed (tenant=%s)",
                    tenant_id,
                    exc_info=True,
                )
    except Exception as exc:
        summary["error"] = repr(exc)
        logger.warning("main_super_admin invariant scan failed", exc_info=True)

    return summary
