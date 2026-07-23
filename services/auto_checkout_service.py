"""WS6: periodic auto-checkout sweep.

Visitors who never scan out stay "on-site" forever, polluting the live
dashboard and the awaiting-checkout selector. For tenants that enable
``tenant_settings.auto_checkout_after_hours`` (default 12; None/0 =
disabled), this sweep closes stale records in BOTH check-in systems:

* ``visit_sessions`` still ``checked_in`` whose ``check_in_time`` is older
  than N hours, and
* ``checkins`` still ``approved`` whose ``approved_at`` (falling back to
  ``date_created`` for legacy rows without an approval timestamp) is older
  than N hours.

Each closed record is stamped exactly like a real checkout —
``check_out_time``/``checked_out_at`` = now, ``check_out_method="auto"``,
``check_out_reason="Auto checkout after {N}h (no checkout recorded)"``,
``auto_checked_out=True`` — so the existing list/detail serializers expose
it with no extra plumbing. Active badges on auto-closed checkins are
revoked (a badge must not outlive its visit).

Scheduled every 15 minutes from ``main.py`` via the textual job ref
``services.auto_checkout_service:run_auto_checkout_sweep`` (APScheduler
pickles to Mongo — see CLAUDE.md gotcha on textual refs).

Idempotency: every write is an ``update_one`` filtered on the still-open
state, so a record another worker (or a real checkout) already closed is
skipped, never double-stamped. The sweep is capped per tenant and per run;
anything left over is picked up by the next 15-minute pass.

This module also owns ``backfill_auto_checkout_default`` — the one-shot
startup backfill that writes the new default (12) into EXISTING
``tenant_settings`` docs. It is gated by a ``backfill_markers`` doc so it
runs once ever: after that, a tenant setting the field back to None/0 is
an explicit "disabled" choice the backfill must not overwrite.
"""

from __future__ import annotations

import logging
import time

from core.database import db
from schemas.imports import CheckinState, CheckOutMethod, VisitStatus
from services.audit_service import record_audit_event
from services.dashboard_cache_service import invalidate_tenant_dashboard_cache

logger = logging.getLogger(__name__)

# Default written into new tenant_settings docs (schema default) and
# backfilled once into existing ones. None/0 disables the sweep.
DEFAULT_AUTO_CHECKOUT_AFTER_HOURS = 12

# Per-tenant, per-collection cap for a single sweep pass. Keeps one huge
# tenant from starving the rest; leftovers roll into the next pass.
MAX_RECORDS_PER_TENANT = 500

# Global cap across all tenants for a single sweep pass.
MAX_RECORDS_PER_RUN = 2000

# Marker _id in the shared ``backfill_markers`` collection (same
# convention as premium_branch_grandfather_backfill).
_BACKFILL_MARKER_ID = "auto_checkout_default_backfill"


def _auto_reason(hours: int) -> str:
    return f"Auto checkout after {hours}h (no checkout recorded)"


async def _nudge_live_dashboard(tenant_id: str) -> None:
    """Best-effort SSE nudge so open dashboards drop the swept visitors
    from their on-site counters immediately. Never raises."""
    try:
        from services.dashboard_stream_service import publish_dashboard_refresh

        await publish_dashboard_refresh(tenant_id)
    except Exception:
        pass


async def _revoke_active_badges(tenant_id: str, checkin_ids: list[str]) -> int:
    """Revoke non-revoked badges attached to auto-closed check-ins.

    Uses ``badge_repo.revoke_badge`` per row so the revocation timestamp
    and any repo-level behaviour stay consistent with manual revokes.
    Failures are logged and skipped — a badge left active is caught by the
    next pass (the checkin itself is already closed, so the badge no
    longer resolves to an on-site visit anyway)."""
    if not checkin_ids:
        return 0
    from repositories.badge_repo import get_badges_by_checkin_ids, revoke_badge

    revoked = 0
    try:
        badges = await get_badges_by_checkin_ids(tenant_id, checkin_ids)
    except Exception:
        logger.warning(
            "auto_checkout: badge lookup failed for tenant %s", tenant_id, exc_info=True
        )
        return 0
    for badge in badges:
        try:
            if badge.id:
                await revoke_badge(badge.id)
                revoked += 1
        except Exception:
            logger.warning(
                "auto_checkout: badge revoke failed (badge %s, tenant %s)",
                badge.id,
                tenant_id,
                exc_info=True,
            )
    return revoked


async def _sweep_visit_sessions(
    tenant_id: str, cutoff: int, now: int, reason: str, limit: int
) -> list[str]:
    """Close stale ``checked_in`` visit_sessions. Returns closed ids."""
    closed: list[str] = []
    cursor = (
        db.visit_sessions.find(
            {
                "tenant_id": tenant_id,
                "status": VisitStatus.CHECKED_IN.value,
                "check_in_time": {"$lt": cutoff},
            },
            projection={"_id": 1},
        )
        .sort("check_in_time", 1)
        .limit(limit)
    )
    async for doc in cursor:
        # Idempotent: the status filter in the update means a session a
        # receptionist checked out between the find and this write is
        # left untouched.
        result = await db.visit_sessions.update_one(
            {"_id": doc["_id"], "status": VisitStatus.CHECKED_IN.value},
            {
                "$set": {
                    "status": VisitStatus.CHECKED_OUT.value,
                    "check_out_time": now,
                    "check_out_method": CheckOutMethod.AUTO.value,
                    "check_out_reason": reason,
                    "auto_checked_out": True,
                    "last_updated": now,
                }
            },
        )
        if result.modified_count:
            closed.append(str(doc["_id"]))
    return closed


async def _sweep_checkins(
    tenant_id: str, cutoff: int, now: int, reason: str, limit: int
) -> list[str]:
    """Close stale ``approved`` checkins. Returns closed ids."""
    closed: list[str] = []
    cursor = (
        db.checkins.find(
            {
                "tenant_id": tenant_id,
                "state": CheckinState.APPROVED.value,
                # approved_at is when the visitor actually became on-site;
                # legacy rows without it fall back to date_created.
                "$or": [
                    {"approved_at": {"$lt": cutoff}},
                    {
                        "approved_at": None,
                        "date_created": {"$lt": cutoff},
                    },
                ],
            },
            projection={"_id": 1},
        )
        .sort("date_created", 1)
        .limit(limit)
    )
    async for doc in cursor:
        result = await db.checkins.update_one(
            {"_id": doc["_id"], "state": CheckinState.APPROVED.value},
            {
                "$set": {
                    "state": CheckinState.CHECKED_OUT.value,
                    "checked_out_at": now,
                    "check_out_method": CheckOutMethod.AUTO.value,
                    "check_out_reason": reason,
                    "auto_checked_out": True,
                    "last_updated": now,
                }
            },
        )
        if result.modified_count:
            closed.append(str(doc["_id"]))
    return closed


async def run_auto_checkout_sweep() -> int:
    """Sweep every enabled tenant once. Returns total records closed."""
    now = int(time.time())
    total_closed = 0

    # Only tenants that have the policy enabled. gt:0 excludes both None
    # and the explicit 0 = disabled value.
    settings_cursor = db.tenant_settings.find(
        {"auto_checkout_after_hours": {"$gt": 0}},
        projection={"tenant_id": 1, "auto_checkout_after_hours": 1},
    )

    async for settings_doc in settings_cursor:
        if total_closed >= MAX_RECORDS_PER_RUN:
            logger.info(
                "auto_checkout: per-run cap (%s) reached — remaining tenants "
                "roll to the next pass",
                MAX_RECORDS_PER_RUN,
            )
            break

        tenant_id = settings_doc.get("tenant_id")
        hours = settings_doc.get("auto_checkout_after_hours")
        if not tenant_id or not isinstance(hours, int) or hours <= 0:
            continue

        cutoff = now - hours * 3600
        reason = _auto_reason(hours)
        budget = min(MAX_RECORDS_PER_TENANT, MAX_RECORDS_PER_RUN - total_closed)

        try:
            session_ids = await _sweep_visit_sessions(
                tenant_id, cutoff, now, reason, budget
            )
            remaining = budget - len(session_ids)
            checkin_ids = (
                await _sweep_checkins(tenant_id, cutoff, now, reason, remaining)
                if remaining > 0
                else []
            )
        except Exception:
            logger.warning(
                "auto_checkout: sweep failed for tenant %s", tenant_id, exc_info=True
            )
            continue

        if not session_ids and not checkin_ids:
            continue

        revoked = await _revoke_active_badges(tenant_id, checkin_ids)
        total_closed += len(session_ids) + len(checkin_ids)

        # One audit event per affected tenant (batched — the sweep can
        # touch hundreds of rows; per-row events would flood the trail).
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="system",
                action="visitor.auto_checked_out",
                resource_type="tenant",
                resource_id=tenant_id,
                tenant_id=tenant_id,
                details={
                    "after_hours": hours,
                    "reason": reason,
                    "visit_session_ids": session_ids,
                    "checkin_ids": checkin_ids,
                    "badges_revoked": revoked,
                },
            )
        except Exception:
            pass

        # One dashboard nudge per affected tenant.
        invalidate_tenant_dashboard_cache(tenant_id)
        await _nudge_live_dashboard(tenant_id)

        logger.info(
            "auto_checkout: tenant %s closed %s visit_sessions + %s checkins "
            "(%s badges revoked, after %sh)",
            tenant_id,
            len(session_ids),
            len(checkin_ids),
            revoked,
            hours,
        )

    return total_closed


async def backfill_auto_checkout_default() -> dict:
    """One-shot: write the new default (12) into existing tenant_settings.

    Historically ``auto_checkout_after_hours`` defaulted to None (feature
    inert), so every existing settings doc carries None. The schema default
    is now 12 for NEW docs; this backfill brings existing docs in line.

    Gated by a ``backfill_markers`` doc so it runs once ever — after the
    marker exists, a None/0 value is an explicit tenant choice ("disabled")
    that a re-run must not overwrite. Safe to call on every boot."""
    marker = await db.backfill_markers.find_one({"_id": _BACKFILL_MARKER_ID})
    if marker:
        return {"skipped": True, "modified": 0}

    result = await db.tenant_settings.update_many(
        {
            "$or": [
                {"auto_checkout_after_hours": None},
                {"auto_checkout_after_hours": {"$exists": False}},
            ]
        },
        {
            "$set": {
                "auto_checkout_after_hours": DEFAULT_AUTO_CHECKOUT_AFTER_HOURS,
                "last_updated": int(time.time()),
            }
        },
    )
    modified = int(getattr(result, "modified_count", 0) or 0)

    await db.backfill_markers.update_one(
        {"_id": _BACKFILL_MARKER_ID},
        {
            "$set": {
                "completed_at": int(time.time()),
                "modified": modified,
            }
        },
        upsert=True,
    )
    logger.info(
        "auto_checkout_default backfill: set %sh on %s tenant_settings docs",
        DEFAULT_AUTO_CHECKOUT_AFTER_HOURS,
        modified,
    )
    return {"skipped": False, "modified": modified}
