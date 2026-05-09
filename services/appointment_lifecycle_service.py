"""Appointment status transitions driven by visit-session lifecycle events.

Centralises the rules for moving an appointment through its lifecycle so
the visit-session, public-registration, and checkout services don't each
have to duplicate the legality checks. Also exposes the periodic
``mark_no_shows`` sweeper that flips abandoned appointments to
``NO_SHOW``.

Transitions (driven by the linked visit-session event):

    SCHEDULED       ──badge issued──▶  CHECKED_IN
    SCHEDULED       ──denied────────▶  CANCELLED
    SCHEDULED       ──no-show sweeper▶ NO_SHOW
    CHECKED_IN      ──visitor checks out▶ CHECKED_OUT  (sets fulfilled_at)

Any other source/target combination is a no-op so this helper is safe to
call defensively whenever a session changes state."""

from __future__ import annotations

import logging
import time
from typing import Optional

from bson import ObjectId

from core.database import db
from repositories.appointment_repo import get_appointment, update_appointment
from schemas.appointment_schema import AppointmentUpdate
from schemas.imports import AppointmentStatus

logger = logging.getLogger(__name__)


# Treat fulfilled (legacy) as equivalent to checked_out so older data
# doesn't get reverted by a fresh check-in flow.
_TERMINAL_STATES = frozenset(
    {
        AppointmentStatus.CHECKED_OUT.value,
        AppointmentStatus.FULFILLED.value,
        AppointmentStatus.CANCELLED.value,
        AppointmentStatus.NO_SHOW.value,
        AppointmentStatus.MISSED.value,
    }
)

# How long after the scheduled time a visitor still has to show up before
# the appointment is auto-flipped to NO_SHOW. 4 hours mirrors the
# overdue-checkout heuristic in the dashboard.
NO_SHOW_GRACE_SECONDS = 4 * 3600


def _enum_value(value) -> Optional[str]:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _allowed(target: AppointmentStatus, current: Optional[str]) -> bool:
    """Return True iff transitioning ``current`` → ``target`` is sensible.

    The check is intentionally permissive — anything already terminal
    stays put, but any non-terminal source can move to any of the
    lifecycle states. The strictness lives in the callers (e.g. only
    invoke ``CHECKED_IN`` from the badge-issuing flow)."""
    if current in _TERMINAL_STATES:
        # Special case: allow CHECKED_IN → CHECKED_OUT even though
        # CHECKED_OUT is terminal, because the visitor's check-out is the
        # natural successor.
        return target is AppointmentStatus.CHECKED_OUT and current == (
            AppointmentStatus.CHECKED_IN.value
        )
    return True


async def transition_appointment(
    appointment_id: Optional[str],
    *,
    tenant_id: str,
    target: AppointmentStatus,
    fulfilled_at: Optional[int] = None,
) -> None:
    """Move an appointment to ``target`` if the transition is legal.

    Silent no-op for missing / invalid / already-terminal appointments —
    this helper is invoked defensively from visit-session writers and
    must not surface errors that block a successful check-in/out."""
    if not appointment_id or not ObjectId.is_valid(appointment_id):
        return
    try:
        existing = await get_appointment(
            {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
        )
    except Exception:
        logger.debug(
            "transition_appointment lookup failed for %s", appointment_id, exc_info=True
        )
        return
    if existing is None:
        return

    current = _enum_value(existing.status)
    if current == target.value:
        return
    if not _allowed(target, current):
        return

    update: AppointmentUpdate
    if target is AppointmentStatus.CHECKED_OUT:
        update = AppointmentUpdate(
            status=target,
            fulfilled_at=fulfilled_at if fulfilled_at is not None else int(time.time()),
        )
    else:
        update = AppointmentUpdate(status=target)

    try:
        await update_appointment(
            {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}, update
        )
    except Exception:
        logger.warning(
            "transition_appointment update failed for %s → %s",
            appointment_id,
            target.value,
            exc_info=True,
        )


# ─── Periodic sweepers ───────────────────────────────────────────────


async def mark_no_shows() -> int:
    """Flip ``SCHEDULED`` appointments past their grace window to ``NO_SHOW``.

    Runs hourly via APScheduler. Returns the number of rows updated so
    operator dashboards / logs can spot anomalies."""
    now = int(time.time())
    cutoff = now - NO_SHOW_GRACE_SECONDS
    try:
        result = await db.expected_appointments.update_many(
            {
                "status": AppointmentStatus.SCHEDULED.value,
                "scheduled_datetime": {"$lt": cutoff},
            },
            {
                "$set": {
                    "status": AppointmentStatus.NO_SHOW.value,
                    "last_updated": now,
                }
            },
        )
    except Exception:
        logger.warning("mark_no_shows sweep failed", exc_info=True)
        return 0
    count = int(getattr(result, "modified_count", 0) or 0)
    if count:
        logger.info("appointment.no_show_sweep marked %s appointments", count)
    return count
