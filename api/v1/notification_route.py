from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from core.response_envelope import document_response
from schemas.notification_schema import (
    NotificationPreferencesUpdate,
    UnreadCountOut,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.notification_service import (
    retrieve_notifications_with_summary,
    mark_notification_read,
    mark_all_notifications_read,
    get_unread_count,
    remove_notification,
    retrieve_or_create_notification_preferences,
    update_user_notification_preferences,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ─── Notification List ─────────────────────────────────────────────


@router.get("")
@document_response(
    message="Notifications fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "title": "Visitor Checked In",
            "body": "John Doe has checked in.",
            "type": "info",
            "read": False,
            "link": "/app/visitors/abc123",
            "date_created": 1712500000,
        }
    ],
    description="List notifications for the authenticated user with optional read filter and pagination.",
    summary="List notifications",
    include_meta=True,
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def list_notifications(
    skip: Annotated[int, Query(ge=0, description="Offset")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    read: Annotated[Optional[bool], Query(description="Filter by read status")] = None,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """List paginated notifications for the authenticated user, enriched with user/tenant snapshots."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    items, total = await retrieve_notifications_with_summary(
        user_id=principal.user_id,
        user_type=user_type,
        read=read,
        skip=skip,
        limit=limit,
    )
    return items


# ─── Unread Count ──────────────────────────────────────────────────


@router.get("/unread-count")
@document_response(
    message="Unread count fetched successfully",
    success_example={"count": 5},
    description="Return the number of unread notifications for the topbar badge.",
    summary="Get unread notification count",
    response_codes={401: "Unauthorized"},
)
async def get_notification_unread_count(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Get unread notification count for badge display."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    count = await get_unread_count(principal.user_id, user_type)
    return UnreadCountOut(count=count)


# ─── Mark Single as Read ──────────────────────────────────────────


@router.patch("/{notification_id}/read")
@document_response(
    message="Notification marked as read",
    description="Mark a single notification as read.",
    summary="Mark notification read",
    response_codes={
        401: "Unauthorized",
        404: "Not found - notification does not exist",
    },
)
async def mark_single_read(
    notification_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Mark a single notification as read."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    return await mark_notification_read(notification_id, principal.user_id, user_type)


# ─── Mark All as Read ─────────────────────────────────────────────


@router.post("/read-all")
@document_response(
    message="All notifications marked as read",
    success_example={"marked_count": 12},
    description="Mark all notifications as read for the authenticated user.",
    summary="Mark all notifications read",
    response_codes={401: "Unauthorized"},
)
async def mark_all_read_endpoint(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Mark all notifications as read."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    count = await mark_all_notifications_read(principal.user_id, user_type)
    return {"marked_count": count}


# ─── Delete Notification ──────────────────────────────────────────


@router.delete("/{notification_id}")
@document_response(
    message="Notification deleted successfully",
    success_example={"deleted": True},
    description="Dismiss / delete a single notification.",
    summary="Delete notification",
    response_codes={
        401: "Unauthorized",
        404: "Not found - notification does not exist",
    },
)
async def delete_notification_endpoint(
    notification_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Delete a notification."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    await remove_notification(notification_id, principal.user_id, user_type)
    return {"deleted": True}


# ─── Notification Preferences ─────────────────────────────────────


@router.get("/preferences")
@document_response(
    message="Notification preferences fetched successfully",
    success_example={
        "email_enabled": True,
        "email_on_incident": True,
        "email_on_visitor_check_in": False,
        "email_on_appointment_reminder": True,
        "email_on_dsr_received": True,
        "email_on_subscription_alert": True,
        "email_on_new_user": False,
    },
    description="Return user notification preferences. Creates defaults on first access.",
    summary="Get notification preferences",
    response_codes={401: "Unauthorized"},
)
async def get_preferences(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Get notification preferences for the authenticated user."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    return await retrieve_or_create_notification_preferences(principal.user_id, user_type)


@router.put("/preferences")
@document_response(
    message="Notification preferences updated successfully",
    description="Update notification preferences. Same shape as GET response.",
    summary="Update notification preferences",
    response_codes={
        401: "Unauthorized",
        422: "Validation error",
    },
)
async def update_preferences(
    data: NotificationPreferencesUpdate,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Update notification preferences."""
    user_type = "admin" if principal.role == "admin" else "system_user"
    return await update_user_notification_preferences(principal.user_id, user_type, data)
