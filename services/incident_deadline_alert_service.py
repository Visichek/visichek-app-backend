"""Periodic NDPC incident-deadline alerting.

Every incident carries ``notification_deadline = date_created + 72h``
(NDPA Section 38). The frontend already surfaces approaching deadlines
via ``GET /v1/incidents/approaching-deadline``, but nothing proactively
notified anyone — the ``notify_incident_deadline`` helper (and the
``email_on_incident`` preference in the UI) had no caller.

``alert_approaching_incident_deadlines`` runs hourly via APScheduler:
it finds incidents within 24 hours of their deadline that haven't been
reported to the NDPC yet, claims each one atomically
(``deadline_alert_sent_at``) so overlapping runs can't double-page, and
notifies the tenant's compliance-relevant roles (dpo, security_officer,
super_admin) through the standard notification path — in-app always,
email per each recipient's ``email_on_incident`` preference.
"""

from __future__ import annotations

import logging
import time

from core.database import db

logger = logging.getLogger(__name__)

DEADLINE_ALERT_WINDOW_SECONDS = 24 * 3600
ALERT_BATCH_LIMIT = 200
# Roles that must know a reporting deadline is closing in.
_ALERT_ROLES = ("dpo", "security_officer", "super_admin")
_MAX_RECIPIENTS_PER_TENANT = 20


async def _alert_recipients_for_tenant(tenant_id: str) -> list:
    """Active dpo / security_officer / super_admin users of the tenant."""
    try:
        from repositories.system_user_repo import get_system_users

        return await get_system_users(
            {
                "tenant_id": tenant_id,
                "role": {"$in": list(_ALERT_ROLES)},
                "is_active": True,
            },
            start=0,
            stop=_MAX_RECIPIENTS_PER_TENANT,
        )
    except Exception:
        logger.warning(
            "incident deadline alerts: recipient lookup failed for tenant %s",
            tenant_id,
            exc_info=True,
        )
        return []


async def alert_approaching_incident_deadlines() -> dict:
    """Hourly APScheduler entry point. Returns counts for observability."""
    now = int(time.time())
    window_end = now + DEADLINE_ALERT_WINDOW_SECONDS
    alerted = 0
    skipped = 0

    try:
        cursor = (
            db.incident_logs.find(
                {
                    "notification_deadline": {"$gte": now, "$lte": window_end},
                    "notification_sent_at": None,
                    "ndpc_notified": {"$ne": True},
                    "deadline_alert_sent_at": None,
                }
            )
            .sort("notification_deadline", 1)
            .limit(ALERT_BATCH_LIMIT)
        )
        approaching = [doc async for doc in cursor]
    except Exception:
        logger.warning("incident deadline alerts: query failed", exc_info=True)
        return {"alerted": 0, "skipped": 0}

    for doc in approaching:
        incident_id = str(doc.get("_id"))
        tenant_id = str(doc.get("tenant_id") or "")

        # Atomic claim so overlapping runs never double-page a tenant.
        try:
            claimed = await db.incident_logs.find_one_and_update(
                {"_id": doc["_id"], "deadline_alert_sent_at": None},
                {"$set": {"deadline_alert_sent_at": now}},
            )
        except Exception:
            logger.warning(
                "incident deadline alerts: claim failed for %s",
                incident_id,
                exc_info=True,
            )
            continue
        if claimed is None:
            skipped += 1
            continue

        recipients = await _alert_recipients_for_tenant(tenant_id)
        if not recipients:
            skipped += 1
            logger.warning(
                "incident deadline alerts: no active dpo/security/super_admin "
                "for tenant %s (incident %s)",
                tenant_id,
                incident_id,
            )
            continue

        from schemas.imports import UserType
        from services.notification_service import notify_incident_deadline

        delivered_any = False
        for user in recipients:
            if not user.id:
                continue
            try:
                await notify_incident_deadline(
                    user_id=user.id,
                    user_type=UserType.SYSTEM_USER,
                    incident_id=incident_id,
                    tenant_id=tenant_id,
                )
                delivered_any = True
            except Exception:
                logger.warning(
                    "incident deadline alerts: notify failed for user %s (incident %s)",
                    user.id,
                    incident_id,
                    exc_info=True,
                )
        if delivered_any:
            alerted += 1
        else:
            skipped += 1

    if alerted or skipped:
        logger.info("incident deadline alerts: alerted=%s skipped=%s", alerted, skipped)
    return {"alerted": alerted, "skipped": skipped}
