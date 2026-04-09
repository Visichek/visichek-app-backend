from __future__ import annotations

import logging
from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException

from repositories.notification_repo import (
    create_notification,
    get_notification,
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
        type=type,
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


async def mark_notification_read(notification_id: str, user_id: str, user_type: str) -> NotificationOut:
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


async def remove_notification(notification_id: str, user_id: str, user_type: str) -> None:
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
        raise HTTPException(status_code=500, detail="Failed to update notification preferences")
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
        logger.warning("Failed to send appointment reminder notification", exc_info=True)


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
