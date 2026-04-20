"""Queued write handlers + per-user precompute loaders for notifications.

Notifications are keyed by ``(user_id, user_type)``. The precompute
loader gets only ``user_id``; ``user_type`` is inferred from whether
the active-user heartbeat has a tenant attached (system_user) or not
(application admin).
"""

from __future__ import annotations

import logging
from typing import Any

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from core.redis_cache import cache_db
from schemas.notification_schema import NotificationPreferencesUpdate
from services.notification_service import (
    get_unread_count,
    mark_all_notifications_read,
    mark_notification_read,
    remove_notification,
    retrieve_notifications_with_summary,
    update_user_notification_preferences,
)

logger = logging.getLogger(__name__)


def _infer_user_type(user_id: str) -> str:
    """Application admins have no tenant_id attached to their active-user
    heartbeat; tenant users do."""
    try:
        raw = cache_db.get(f"active:user:{user_id}")
    except Exception:
        raw = None
    tenant_id = raw if isinstance(raw, str) else ""
    return "admin" if not tenant_id else "system_user"


def _enqueue_user_refresh(user_id: str, tenant_id: str) -> None:
    if not user_id:
        return
    qm = QueueManager.get_instance()
    for resource in ("notifications.list", "notifications.unread_count"):
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={
                    "tenant_id": tenant_id or "",
                    "resource": resource,
                    "user_id": user_id,
                },
            )
        except Exception:
            logger.warning(
                "notification_writer: refresh enqueue failed user=%s resource=%s",
                user_id,
                resource,
                exc_info=True,
            )


@write_handler("notification.mark_read")
async def _notification_mark_read(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or ""
    user_type = data.get("user_type", "") or "system_user"
    tenant_id = data.get("tenant_id", "") or ""
    result = await mark_notification_read(
        notification_id=resource_id, user_id=user_id, user_type=user_type
    )
    _enqueue_user_refresh(user_id, tenant_id)
    return {"id": result.id, "read": result.read}


@write_handler("notification.mark_all_read")
async def _notification_mark_all_read(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or resource_id
    user_type = data.get("user_type", "") or "system_user"
    tenant_id = data.get("tenant_id", "") or ""
    count = await mark_all_notifications_read(user_id=user_id, user_type=user_type)
    _enqueue_user_refresh(user_id, tenant_id)
    return {"user_id": user_id, "marked_count": count}


@write_handler("notification.delete")
async def _notification_delete(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.get("user_id", "") or ""
    user_type = data.get("user_type", "") or "system_user"
    tenant_id = data.get("tenant_id", "") or ""
    await remove_notification(
        notification_id=resource_id, user_id=user_id, user_type=user_type
    )
    _enqueue_user_refresh(user_id, tenant_id)
    return {"id": resource_id, "deleted": True}


@write_handler("notification.update_preferences")
async def _notification_update_preferences(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.pop("user_id", "") or resource_id
    user_type = data.pop("user_type", "") or "system_user"
    data.pop("tenant_id", None)
    prefs = NotificationPreferencesUpdate(**data)
    result = await update_user_notification_preferences(
        user_id=user_id, user_type=user_type, data=prefs
    )
    return {"id": result.id, "user_id": user_id}


@register_precompute("notifications.list", scope=PrecomputeScope.USER)
async def _precompute_notifications_list(user_id: str) -> Any:
    user_type = _infer_user_type(user_id)
    items, total = await retrieve_notifications_with_summary(
        user_id=user_id, user_type=user_type, skip=0, limit=20
    )
    return {
        "items": [
            i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
            for i in items
        ],
        "total": total,
    }


@register_precompute("notifications.unread_count", scope=PrecomputeScope.USER)
async def _precompute_notifications_unread_count(user_id: str) -> Any:
    user_type = _infer_user_type(user_id)
    count = await get_unread_count(user_id=user_id, user_type=user_type)
    return {"count": count}
