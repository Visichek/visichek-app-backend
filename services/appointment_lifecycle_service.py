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


# How far ahead of the scheduled time the reminder fires. The sweeper
# runs every 5 minutes via APScheduler, so a host is reminded roughly
# 25-30 minutes before the visit.
REMINDER_WINDOW_SECONDS = 30 * 60
REMINDER_BATCH_LIMIT = 200


async def _resolve_reminder_recipient(host_id: str) -> tuple[Optional[str], dict]:
    """Resolve who should receive the reminder for ``host_id``.

    Returns ``(system_user_id, host_contact)``:

    * hosts backed by a tenant system_user (``source_system_user_id``) →
      that user id, so the notification path applies their preferences;
    * dedicated hosts (host record only) → ``(None, {name, email})`` so
      the caller can email the host record's address directly;
    * legacy appointments whose ``host_id`` predates the hosts collection
      and points straight at a system_user → that user id.
    """
    if not ObjectId.is_valid(host_id):
        return None, {}

    try:
        from repositories.host_repo import get_host

        host = await get_host({"_id": ObjectId(host_id)})
        if host is not None:
            if host.source_system_user_id:
                return host.source_system_user_id, {}
            return None, {
                "name": getattr(host, "name", None) or "",
                "email": getattr(host, "email", None) or "",
            }
    except Exception:
        logger.debug("reminder: host lookup failed for %s", host_id, exc_info=True)

    try:
        from repositories.system_user_repo import get_system_user

        user = await get_system_user({"_id": ObjectId(host_id)})
        if user is not None and user.id:
            return user.id, {}
    except Exception:
        logger.debug(
            "reminder: legacy system_user lookup failed for %s",
            host_id,
            exc_info=True,
        )
    return None, {}


async def _send_dedicated_host_reminder(
    *,
    host_email: str,
    host_name: str,
    visitor_name: str,
    appointment_id: str,
) -> None:
    """Email a reminder to a dedicated host (no system_user account).

    Dedicated hosts have no notification-preferences row, so the email
    goes out unconditionally — it's the only channel that can reach them.
    """
    from core.email.manager import EmailManager
    from core.email.types import EmailDispatchRequest

    manager = EmailManager.get_instance()
    if not manager.has_transport():
        return
    await manager.send_template(
        EmailDispatchRequest(
            to_email=host_email,
            template_key="notif_appointment_reminder",
            context={
                "recipient_name": host_name or host_email,
                "title": "Upcoming Appointment",
                "body": (
                    f"You have an appointment with {visitor_name} "
                    "in about 30 minutes."
                ),
                "link": "",
                "visitor_name": visitor_name,
                "appointment_id": appointment_id,
            },
            dispatch="auto",
        )
    )


async def send_due_appointment_reminders() -> dict:
    """Remind hosts about appointments starting within the next 30 minutes.

    Runs every 5 minutes via APScheduler. Each appointment is claimed
    atomically (``reminder_sent_at`` flips from absent/None to now) so a
    concurrent run or web/worker overlap can never double-send. Rows are
    never retried after a claim — a failed notification is logged, not
    re-queued, because a late duplicate reminder is worse than a missed
    one this close to the visit.
    """
    now = int(time.time())
    window_end = now + REMINDER_WINDOW_SECONDS
    sent = 0
    skipped = 0

    try:
        cursor = (
            db.expected_appointments.find(
                {
                    "status": AppointmentStatus.SCHEDULED.value,
                    "scheduled_datetime": {"$gte": now, "$lte": window_end},
                    "reminder_sent_at": None,
                }
            )
            .sort("scheduled_datetime", 1)
            .limit(REMINDER_BATCH_LIMIT)
        )
        due = [doc async for doc in cursor]
    except Exception:
        logger.warning("appointment reminders: query failed", exc_info=True)
        return {"sent": 0, "skipped": 0}

    for doc in due:
        appointment_id = str(doc.get("_id"))
        # Atomic claim — only one sweep instance wins this row.
        try:
            claimed = await db.expected_appointments.find_one_and_update(
                {"_id": doc["_id"], "reminder_sent_at": None},
                {"$set": {"reminder_sent_at": now}},
            )
        except Exception:
            logger.warning(
                "appointment reminders: claim failed for %s",
                appointment_id,
                exc_info=True,
            )
            continue
        if claimed is None:
            skipped += 1
            continue

        visitor_name = (
            str(doc.get("visitor_name_snapshot") or "").strip() or "a visitor"
        )
        tenant_id = str(doc.get("tenant_id") or "")
        host_id = str(doc.get("host_id") or "")

        try:
            recipient_user_id, host_contact = await _resolve_reminder_recipient(
                host_id
            )
            if recipient_user_id:
                from schemas.imports import UserType
                from services.notification_service import notify_appointment_reminder

                await notify_appointment_reminder(
                    user_id=recipient_user_id,
                    user_type=UserType.SYSTEM_USER,
                    appointment_id=appointment_id,
                    visitor_name=visitor_name,
                    tenant_id=tenant_id,
                )
                sent += 1
            elif host_contact.get("email"):
                await _send_dedicated_host_reminder(
                    host_email=host_contact["email"],
                    host_name=host_contact.get("name", ""),
                    visitor_name=visitor_name,
                    appointment_id=appointment_id,
                )
                sent += 1
            else:
                skipped += 1
                logger.info(
                    "appointment reminders: no reachable host for %s (host_id=%s)",
                    appointment_id,
                    host_id,
                )
        except Exception:
            logger.warning(
                "appointment reminders: notify failed for %s",
                appointment_id,
                exc_info=True,
            )

    if sent or skipped:
        logger.info(
            "appointment reminders: sent=%s skipped=%s window=%ss",
            sent,
            skipped,
            REMINDER_WINDOW_SECONDS,
        )
    return {"sent": sent, "skipped": skipped}
