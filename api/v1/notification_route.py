from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.notification_schema import (
    NotificationPreferencesUpdate,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.notification_service import (
    retrieve_notifications_with_summary,
    get_unread_count,
    retrieve_or_create_notification_preferences,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _scope_for_user(principal: AuthPrincipal) -> str:
    tenant_id = principal.tenant_id or "_"
    return f"{PrecomputeScope.TENANT.value}:{tenant_id}:{PrecomputeScope.USER.value}:{principal.user_id}"


def _user_type(principal: AuthPrincipal) -> str:
    return "admin" if principal.role == "admin" else "system_user"


# ─── Notification List ─────────────────────────────────────────────


@router.get("")
@document_response(
    message="Notifications fetched successfully",
    description="Default page served from the per-user precompute cache; filtered reads go live.",
    summary="List notifications",
    include_meta=True,
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def list_notifications(
    skip: Annotated[int, Query(ge=0, description="Offset")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
    read: Annotated[Optional[bool], Query(description="Filter by read status")] = None,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    user_type = _user_type(principal)
    if read is None and skip == 0 and limit == 20:
        cached = await get_or_compute(
            scope_key=_scope_for_user(principal),
            resource="notifications.list",
            ttl=30,
            loader=lambda: _load_notifications(principal.user_id, user_type),
        )
        return cached.get("items", [])
    items, _total = await retrieve_notifications_with_summary(
        user_id=principal.user_id,
        user_type=user_type,
        read=read,
        skip=skip,
        limit=limit,
    )
    return items


async def _load_notifications(user_id: str, user_type: str) -> dict:
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


# ─── Unread Count ──────────────────────────────────────────────────


@router.get("/unread-count")
@document_response(
    message="Unread count fetched successfully",
    success_example={"count": 5},
    description="Served from the per-user precompute cache (TTL 15s — this is a topbar badge).",
    summary="Get unread notification count",
    response_codes={401: "Unauthorized"},
)
async def get_notification_unread_count(
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    user_type = _user_type(principal)
    return await get_or_compute(
        scope_key=_scope_for_user(principal),
        resource="notifications.unread_count",
        ttl=15,
        loader=lambda: _load_unread_count(principal.user_id, user_type),
    )


async def _load_unread_count(user_id: str, user_type: str) -> dict:
    count = await get_unread_count(user_id=user_id, user_type=user_type)
    return {"count": count}


# ─── Mark Single as Read ──────────────────────────────────────────


@router.patch("/{notification_id}/read")
@document_response(
    message="Mark-read queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue marking a single notification as read.",
    summary="Mark notification read (async)",
    response_codes={401: "Unauthorized"},
)
async def mark_single_read(
    notification_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await enqueue_write(
        writer_key="notification.mark_read",
        payload={
            "user_id": principal.user_id,
            "user_type": _user_type(principal),
            "tenant_id": principal.tenant_id or "",
        },
        resource_type="notification",
        resource_id=notification_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Mark All as Read ─────────────────────────────────────────────


@router.post("/read-all")
@document_response(
    message="Mark-all-read queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue marking all notifications as read for the authenticated user.",
    summary="Mark all notifications read (async)",
    response_codes={401: "Unauthorized"},
)
async def mark_all_read_endpoint(
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await enqueue_write(
        writer_key="notification.mark_all_read",
        payload={
            "user_id": principal.user_id,
            "user_type": _user_type(principal),
            "tenant_id": principal.tenant_id or "",
        },
        resource_type="notification",
        resource_id=principal.user_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Delete Notification ──────────────────────────────────────────


@router.delete("/{notification_id}")
@document_response(
    message="Notification deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a notification dismissal.",
    summary="Delete notification (async)",
    response_codes={401: "Unauthorized"},
)
async def delete_notification_endpoint(
    notification_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await enqueue_write(
        writer_key="notification.delete",
        payload={
            "user_id": principal.user_id,
            "user_type": _user_type(principal),
            "tenant_id": principal.tenant_id or "",
        },
        resource_type="notification",
        resource_id=notification_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Notification Preferences ─────────────────────────────────────


@router.get("/preferences")
@document_response(
    message="Notification preferences fetched successfully",
    description="Personal preferences — low volume, served live.",
    summary="Get notification preferences",
    response_codes={401: "Unauthorized"},
)
async def get_preferences(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await retrieve_or_create_notification_preferences(
        principal.user_id, _user_type(principal)
    )


@router.put("/preferences")
@document_response(
    message="Notification preferences update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue an update to notification preferences.",
    summary="Update notification preferences (async)",
    response_codes={401: "Unauthorized", 422: "Validation error"},
)
async def update_preferences(
    data: NotificationPreferencesUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    payload = data.model_dump(exclude_none=True)
    payload["user_id"] = principal.user_id
    payload["user_type"] = _user_type(principal)
    payload["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="notification.update_preferences",
        payload=payload,
        resource_type="notification_preferences",
        resource_id=principal.user_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
