"""Trial integrity watchdog.

Enforces the platform invariant: **every tenant may redeem at most one
free trial across their entire lifetime.**

That invariant is defended in three layers:

1. **Application layer** — ``services/trial_code_service.py`` rejects a
   second claim / checkout with ``409 TRIAL_ALREADY_USED`` via
   ``get_tenant_redeemed_trial``.
2. **Database layer** — the ``tenant_used_trial_unique`` partial-unique
   index on ``trial_codes`` (see ``core/indexes.py``) hard-rejects a
   second ``status="used"`` row per tenant, closing the concurrency race
   the app-level check alone leaves open.
3. **This watchdog** — a periodic safety net that detects any tenant that
   nonetheless ended up with more than one redeemed trial (e.g. legacy
   rows created before the index existed). It does NOT auto-revoke
   billing — picking which redemption to void is unsafe — it raises a
   HIGH-priority support case and an audit event so an operator can
   reconcile it by hand. The index is the prevention; this is the alarm.

Registered on a 6-hourly APScheduler interval in ``main.py`` via the
textual reference ``services.trial_watchdog_service:reconcile_trial_integrity``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.database import db
from schemas.imports import (
    SupportCaseCategory,
    SupportCasePriority,
    SupportCaseStatus,
)
from schemas.support_case_schema import SupportCaseCreate
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

TRIAL_CODE_COLLECTION = "trial_codes"
SUPPORT_CASE_COLLECTION = "support_cases"

_DUPLICATE_TRIAL_CASE_SUBJECT = "Trial integrity: tenant has multiple redeemed trials"


async def find_duplicate_used_trials() -> List[Dict[str, Any]]:
    """Return tenants that have more than one redeemed ("used") trial.

    Each entry: ``{"tenant_id": str, "count": int, "trial_ids": [str, ...]}``.
    A healthy platform returns an empty list. Exposed separately so it can
    be run as a one-off pre-deploy check (the partial-unique index will
    fail to build if any tenant already has duplicates).
    """
    pipeline = [
        {"$match": {"status": "used"}},
        {
            "$group": {
                "_id": "$tenant_id",
                "count": {"$sum": 1},
                "ids": {"$push": "$_id"},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]
    out: List[Dict[str, Any]] = []
    async for row in db[TRIAL_CODE_COLLECTION].aggregate(pipeline):
        out.append(
            {
                "tenant_id": str(row.get("_id")),
                "count": int(row.get("count", 0)),
                "trial_ids": [str(i) for i in row.get("ids", [])],
            }
        )
    return out


async def _open_duplicate_trial_support_case(
    tenant_id: str, *, count: int, trial_ids: List[str]
) -> bool:
    """Open an idempotent HIGH-priority support case for a tenant.

    Returns True if a NEW case was opened, False if one was already open
    (so the periodic run doesn't spam duplicates).
    """
    try:
        from schemas.support_case_schema import OPEN_STATUSES

        existing = await db[SUPPORT_CASE_COLLECTION].find_one(
            {
                "tenant_id": tenant_id,
                "subject": _DUPLICATE_TRIAL_CASE_SUBJECT,
                "status": {"$in": list(OPEN_STATUSES)},
            }
        )
        if existing:
            return False

        case = SupportCaseCreate(
            tenant_id=tenant_id,
            opened_by="system",
            opened_by_role="admin",
            subject=_DUPLICATE_TRIAL_CASE_SUBJECT,
            description=(
                "The trial integrity watchdog found this tenant has "
                f"{count} redeemed (status='used') trial codes, but the "
                "platform invariant allows only one per tenant for their "
                "entire lifetime.\n\n"
                f"Affected trial_code ids: {', '.join(trial_ids)}\n\n"
                "This was most likely caused by rows created before the "
                "tenant_used_trial_unique DB index was in place. The index "
                "now prevents NEW duplicates; this case flags the existing "
                "ones for manual reconciliation.\n\n"
                "Recovery: review the trial_codes rows above, decide which "
                "redemption is legitimate, and reconcile the extras (and any "
                "trial subscriptions / invoices they spawned) by hand. Do NOT "
                "blindly delete — they may be linked to live subscriptions.\n\n"
                "Auto-opened by services/trial_watchdog_service.py. Re-used on "
                "every run while the tenant remains in this state."
            ),
            category=SupportCaseCategory.BILLING,
            priority=SupportCasePriority.HIGH,
            status=SupportCaseStatus.OPEN,
        )
        await db[SUPPORT_CASE_COLLECTION].insert_one(case.model_dump())
        logger.warning(
            "trial integrity: opened HIGH-priority support case for tenant=%s "
            "(%s redeemed trials)",
            tenant_id,
            count,
        )
        return True
    except Exception:
        logger.warning(
            "trial integrity: auto-open support case failed (tenant=%s)",
            tenant_id,
            exc_info=True,
        )
        return False


async def reconcile_trial_integrity() -> Dict[str, Any]:
    """Scan ``trial_codes`` for tenants with >1 redeemed trial; alert.

    Detection + alerting only — never mutates billing. Returns a summary
    dict suitable for logging / the admin CLI:
    ``{"scanned_violations": N, "cases_opened": M}``.
    """
    try:
        violations = await find_duplicate_used_trials()
    except Exception:
        logger.exception("trial integrity: failed to scan trial_codes")
        return {"scanned_violations": 0, "cases_opened": 0, "error": True}

    cases_opened = 0
    for v in violations:
        tenant_id = v["tenant_id"]
        opened = await _open_duplicate_trial_support_case(
            tenant_id,
            count=v["count"],
            trial_ids=v["trial_ids"],
        )
        if opened:
            cases_opened += 1
        try:
            await record_audit_event(
                actor_id="system:trial_watchdog",
                actor_role="admin",
                action="trial.integrity_violation",
                resource_type="tenant",
                resource_id=tenant_id,
                tenant_id=tenant_id,
                details={
                    "redeemed_trial_count": v["count"],
                    "trial_ids": v["trial_ids"],
                },
            )
        except Exception:
            pass

    if violations:
        logger.warning(
            "trial integrity watchdog: %s tenant(s) with duplicate redeemed "
            "trials (%s new support case(s) opened)",
            len(violations),
            cases_opened,
        )
    return {"scanned_violations": len(violations), "cases_opened": cases_opened}
