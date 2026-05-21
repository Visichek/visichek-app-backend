"""Queued write handlers for notifications.

Notifications are per-user, low-volume, and require strict freshness
(badge counts, deadlines, security alerts). They deliberately do NOT
sit behind a precompute cache: the per-user precompute scope was
producing wrong results because the loader had to infer ``user_type``
from a Redis heartbeat that races with worker pickup. The route reads
straight from MongoDB and the HttpCacheMiddleware is configured to
bypass ``/v1/notifications/*`` so synchronous direct-DB writes from
``send_notification`` are immediately visible.
"""

from __future__ import annotations

import logging
from typing import Any

from core.queue.write_pipeline import write_handler
from schemas.imports import UserType
from schemas.notification_schema import NotificationPreferencesUpdate
from services.notification_service import (
    mark_all_notifications_read,
    mark_notification_read,
    remove_notification,
    update_user_notification_preferences,
)
from services.notification_stream_service import publish_notification_changed

logger = logging.getLogger(__name__)


@write_handler("notification.mark_read")
async def _notification_mark_read(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or ""
    user_type = UserType(data.get("user_type", "") or "system_user")
    result = await mark_notification_read(
        notification_id=resource_id, user_id=user_id, user_type=user_type
    )
    await publish_notification_changed(user_id=user_id, user_type=user_type)
    return {"id": result.id, "read": result.read}


@write_handler("notification.mark_all_read")
async def _notification_mark_all_read(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or resource_id
    user_type = UserType(data.get("user_type", "") or "system_user")
    count = await mark_all_notifications_read(user_id=user_id, user_type=user_type)
    await publish_notification_changed(user_id=user_id, user_type=user_type)
    return {"user_id": user_id, "marked_count": count}


@write_handler("notification.delete")
async def _notification_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or ""
    user_type = UserType(data.get("user_type", "") or "system_user")
    await remove_notification(
        notification_id=resource_id, user_id=user_id, user_type=user_type
    )
    await publish_notification_changed(user_id=user_id, user_type=user_type)
    return {"id": resource_id, "deleted": True}


@write_handler("notification.update_preferences")
async def _notification_update_preferences(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.pop("user_id", "") or resource_id
    user_type = UserType(data.pop("user_type", "") or "system_user")
    data.pop("tenant_id", None)
    prefs = NotificationPreferencesUpdate(**data)
    result = await update_user_notification_preferences(
        user_id=user_id, user_type=user_type, data=prefs
    )
    return {"id": result.id, "user_id": user_id}
