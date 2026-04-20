from __future__ import annotations

import logging
from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException

from repositories.notification_repo import (
    create_notification,
    get_notifications,
    count_notifications,
    update_notification,
    mark_all_read,
    delete_notification,
    create_notification_preferences,
    get_notification_preferences,
    update_notification_preferences,
)
from schemas.notification_schema import (
    NotificationCreate,
    NotificationUpdate,
    NotificationOut,
    NotificationWithSummaryOut,
    NotificationPreferencesCreate,
    NotificationPreferencesUpdate,
    NotificationPreferencesOut,
)
from schemas.notification_schema import NotificationType

logger = logging.getLogger(__name__)


# --- Notification CRUD ---


async def send_notification(
    user_id: str,
    user_type: str,
    title: str,
    body: str,
    type: str = "info",
    link: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> NotificationOut:
    """Create and deliver a notification to a user. Fire-and-forget safe."""
    data = NotificationCreate(
        user_id=user_id,
        user_type=user_type,
        title=title,
        body=body,
        type=NotificationType(type),
        link=link,
        tenant_id=tenant_id,
    )
    return await create_notification(data)


async def retrieve_notifications(
    user_id: str,
    user_type: str,
    read: Optional[bool] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[List[NotificationOut], int]:
    """Retrieve paginated notifications with optional read filter."""
    filter_dict: dict = {"user_id": user_id, "user_type": user_type}
    if read is not None:
        filter_dict["read"] = read

    items = await get_notifications(filter_dict, skip=skip, limit=limit)
    total = await count_notifications(filter_dict)
    return items, total


async def _enrich_notification(notif: NotificationOut) -> NotificationWithSummaryOut:
    import asyncio
    from services.summary_resolver import resolve_user_summary, resolve_tenant_summary

    user_summary, tenant_summary = await asyncio.gather(
        resolve_user_summary(notif.user_id, user_type=notif.user_type),
        resolve_tenant_summary(notif.tenant_id),
    )
    data = notif.model_dump(by_alias=False)
    data["user_summary"] = user_summary
    data["tenant_summary"] = tenant_summary
    return NotificationWithSummaryOut(**data)


async def retrieve_notifications_with_summary(
    user_id: str,
    user_type: str,
    read: Optional[bool] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[List[NotificationWithSummaryOut], int]:
    import asyncio

    items, total = await retrieve_notifications(
        user_id=user_id, user_type=user_type, read=read, skip=skip, limit=limit
    )
    enriched = list(await asyncio.gather(*[_enrich_notification(n) for n in items]))
    return enriched, total


async def mark_notification_read(
    notification_id: str, user_id: str, user_type: str
) -> NotificationOut:
    """Mark a single notification as read."""
    if not ObjectId.is_valid(notification_id):
        raise HTTPException(status_code=400, detail="Invalid notification ID format")

    result = await update_notification(
        {"_id": ObjectId(notification_id), "user_id": user_id, "user_type": user_type},
        NotificationUpdate(read=True),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Notification not found")
    return result


async def mark_all_notifications_read(user_id: str, user_type: str) -> int:
    """Mark all notifications as read for a user. Returns count modified."""
    return await mark_all_read(user_id, user_type)


async def get_unread_count(user_id: str, user_type: str) -> int:
    """Get unread notification count for badge display."""
    return await count_notifications(
        {"user_id": user_id, "user_type": user_type, "read": False}
    )


async def remove_notification(
    notification_id: str, user_id: str, user_type: str
) -> None:
    """Delete a single notification."""
    if not ObjectId.is_valid(notification_id):
        raise HTTPException(status_code=400, detail="Invalid notification ID format")

    result = await delete_notification(
        {"_id": ObjectId(notification_id), "user_id": user_id, "user_type": user_type}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")


# --- Notification Preferences ---


async def retrieve_or_create_notification_preferences(
    user_id: str,
    user_type: str,
) -> NotificationPreferencesOut:
    """Get notification preferences, auto-creating defaults on first access."""
    existing = await get_notification_preferences(
        {"user_id": user_id, "user_type": user_type}
    )
    if existing:
        return existing

    defaults = NotificationPreferencesCreate(user_id=user_id, user_type=user_type)
    return await create_notification_preferences(defaults)


async def update_user_notification_preferences(
    user_id: str,
    user_type: str,
    data: NotificationPreferencesUpdate,
) -> NotificationPreferencesOut:
    """Update notification preferences, creating defaults first if needed."""
    await retrieve_or_create_notification_preferences(user_id, user_type)

    result = await update_notification_preferences(
        {"user_id": user_id, "user_type": user_type},
        data,
    )
    if not result:
        raise HTTPException(
            status_code=500, detail="Failed to update notification preferences"
        )
    return result


# --- Notification Trigger Helpers ---


async def notify_incident_deadline(
    user_id: str,
    user_type: str,
    incident_id: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget: notify about NDPC 72-hour deadline approaching."""
    try:
        await send_notification(
            user_id=user_id,
            user_type=user_type,
            title="Incident Deadline Approaching",
            body="An incident is approaching the NDPC 72-hour reporting deadline.",
            type="warning",
            link=f"/app/incidents/{incident_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send incident deadline notification", exc_info=True)


async def notify_visitor_check_in(
    host_user_id: str,
    user_type: str,
    visitor_name: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget: notify host about visitor check-in."""
    try:
        await send_notification(
            user_id=host_user_id,
            user_type=user_type,
            title="Visitor Checked In",
            body=f"{visitor_name} has checked in and is waiting for you.",
            type="info",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send visitor check-in notification", exc_info=True)


async def notify_appointment_reminder(
    user_id: str,
    user_type: str,
    appointment_id: str,
    visitor_name: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget: 30-minute appointment reminder."""
    try:
        await send_notification(
            user_id=user_id,
            user_type=user_type,
            title="Upcoming Appointment",
            body=f"You have an appointment with {visitor_name} in 30 minutes.",
            type="info",
            link=f"/app/appointments/{appointment_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning(
            "Failed to send appointment reminder notification", exc_info=True
        )


async def notify_dsr_submitted(
    user_id: str,
    user_type: str,
    dsr_id: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget: notify about new data subject request."""
    try:
        await send_notification(
            user_id=user_id,
            user_type=user_type,
            title="New Data Subject Request",
            body="A new data subject request has been submitted and requires attention.",
            type="info",
            link=f"/app/dsr/{dsr_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send DSR notification", exc_info=True)


async def notify_subscription_alert(
    user_id: str,
    user_type: str,
    message: str,
    tenant_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: subscription expiring or payment failed."""
    try:
        await send_notification(
            user_id=user_id,
            user_type=user_type,
            title="Subscription Alert",
            body=message,
            type="warning",
            link="/app/billing",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send subscription alert notification", exc_info=True)


async def notify_new_user_added(
    admin_user_id: str,
    user_type: str,
    new_user_name: str,
    tenant_id: str,
) -> None:
    """Fire-and-forget: notify about new user added to tenant."""
    try:
        await send_notification(
            user_id=admin_user_id,
            user_type=user_type,
            title="New User Added",
            body=f"{new_user_name} has been added to your organization.",
            type="success",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send new user notification", exc_info=True)


# --- Check-In Notifications ---


async def notify_checkin_pending_approval(
    tenant_id: str,
    checkin_id: str,
    visitor_name: str,
    verified: bool,
    purpose: str,
    host_employee_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: notify receptionists about pending check-in."""
    try:
        from repositories.system_user_repo import get_system_users

        # Get all receptionists for the tenant
        receptionists = await get_system_users(
            {"tenant_id": tenant_id, "role": "receptionist"}
        )

        # Send to each receptionist
        for receptionist in receptionists:
            try:
                await send_notification(
                    user_id=receptionist.id or "",
                    user_type="system_user",
                    title="Pending Check-In Approval",
                    body=f"{visitor_name} ({purpose}) is awaiting approval.",
                    type="info",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify receptionist {receptionist.id}", exc_info=True
                )
    except Exception:
        logger.warning(
            "Failed to send check-in pending approval notification", exc_info=True
        )


async def notify_checkin_approved(
    tenant_id: str,
    checkin_id: str,
    badge_id: str,
    visitor_name: str,
    approved_by_user_id: str,
    host_employee_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: notify about approved check-in and badge issuance."""
    try:
        from repositories.system_user_repo import get_system_users

        # Get all receptionists for the tenant
        receptionists = await get_system_users(
            {"tenant_id": tenant_id, "role": "receptionist"}
        )

        for receptionist in receptionists:
            try:
                await send_notification(
                    user_id=receptionist.id or "",
                    user_type="system_user",
                    title="Check-In Approved",
                    body=f"Badge issued for {visitor_name}. Badge ID: {badge_id}",
                    type="success",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify receptionist {receptionist.id}", exc_info=True
                )
    except Exception:
        logger.warning("Failed to send check-in approved notification", exc_info=True)


async def notify_checkin_rejected(
    tenant_id: str,
    checkin_id: str,
    visitor_name: str,
    rejected_by_user_id: str,
    reason: str,
    host_employee_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: notify about rejected check-in."""
    try:
        from repositories.system_user_repo import get_system_users

        # Get all receptionists for the tenant
        receptionists = await get_system_users(
            {"tenant_id": tenant_id, "role": "receptionist"}
        )

        for receptionist in receptionists:
            try:
                await send_notification(
                    user_id=receptionist.id or "",
                    user_type="system_user",
                    title="Check-In Rejected",
                    body=f"Check-in for {visitor_name} was rejected. Reason: {reason}",
                    type="warning",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify receptionist {receptionist.id}", exc_info=True
                )
    except Exception:
        logger.warning("Failed to send check-in rejected notification", exc_info=True)


# --- Queued Job Failure Notification ---


_VERB_PAST_TENSE = {
    "create": "create",
    "update": "update",
    "delete": "delete",
    "archive": "archive",
    "unarchive": "restore",
    "publish": "publish",
    "unpublish": "unpublish",
    "activate": "activate",
    "deactivate": "deactivate",
    "disable": "disable",
    "enable": "enable",
    "cancel": "cancel",
    "approve": "approve",
    "reject": "reject",
    "reset": "reset",
    "suspend": "suspend",
    "offboard": "offboard",
    "assign": "assign",
    "revoke": "revoke",
    "subscribe": "subscribe to",
    "change_plan": "change plan for",
    "clone": "clone",
    "upload": "upload",
}


def _format_action_from_writer_key(writer_key: str) -> str:
    """Turn ``discount.delete`` into ``Couldn't delete discount``."""
    resource, _, verb = writer_key.partition(".")
    if not verb:
        return f"Action failed: {writer_key}"
    resource_label = resource.replace("_", " ")
    verb_label = _VERB_PAST_TENSE.get(verb, verb.replace("_", " "))
    return f"Couldn't {verb_label} {resource_label}"


def _extract_failure_message(exception: BaseException) -> tuple[str, Optional[str]]:
    """Return ``(body, error_code)`` for a failed job exception.

    ``AppException`` / ``HTTPException`` carry user-facing detail that the
    user is allowed to see; anything else gets a generic message with the
    job id surfaced so support can trace it.
    """
    from core.errors import AppException

    if isinstance(exception, AppException):
        detail: dict = exception.detail if isinstance(exception.detail, dict) else {}
        message = detail.get("message")
        code = detail.get("code")
        return (message or "The operation failed.", code)

    if isinstance(exception, HTTPException):
        raw_detail = getattr(exception, "detail", None)
        if isinstance(raw_detail, str) and raw_detail:
            return (raw_detail, None)
        if isinstance(raw_detail, dict):
            message = raw_detail.get("message") or raw_detail.get("detail")
            if isinstance(message, str) and message:
                return (message, raw_detail.get("code"))
        return ("The operation failed.", None)

    return (
        "The operation failed unexpectedly. Support has been notified.",
        None,
    )


def _user_type_for_role(role: Optional[str]) -> Optional[str]:
    """Map an auth role to the ``user_type`` used on notifications."""
    if not role:
        return None
    from security.principal import TENANT_USER_ROLES

    if role in TENANT_USER_ROLES:
        return "system_user"
    if role == "admin":
        return "admin"
    if role == "user":
        return "user"
    return None


async def notify_job_failure(
    task_id: str,
    writer_key: str,
    exception: BaseException,
) -> None:
    """Fire-and-forget: notify the user who enqueued a failed write.

    Looks up ``queue_job_log`` by ``task_id`` to recover the actor and
    delivers a notification explaining what went wrong. Silent if the job
    has no actor (system-initiated write) or the actor's role isn't
    notifiable.
    """
    try:
        from repositories.queue_job_log_repo import get_job_log_by_task_id

        log_entry = await get_job_log_by_task_id(task_id)
        if log_entry is None:
            logger.warning(
                "notify_job_failure: no queue_job_log for task_id=%s", task_id
            )
            return
        if not log_entry.actor_id:
            return

        user_type = _user_type_for_role(log_entry.actor_role)
        if not user_type:
            return

        body, _ = _extract_failure_message(exception)
        title = _format_action_from_writer_key(writer_key)

        await send_notification(
            user_id=log_entry.actor_id,
            user_type=user_type,
            title=title,
            body=body,
            type="error",
            link=f"/app/jobs/{task_id}",
            tenant_id=log_entry.tenant_id,
        )
        logger.info(
            "notify_job_failure: delivered task_id=%s writer=%s actor=%s",
            task_id,
            writer_key,
            log_entry.actor_id,
        )
    except Exception:
        logger.warning(
            "notify_job_failure: failed to deliver notification task_id=%s",
            task_id,
            exc_info=True,
        )
