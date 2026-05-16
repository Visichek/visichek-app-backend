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


# Tenant-wide roles whose users should see every pending check-in regardless
# of department. ``dept_admin`` is scoped to a single department and is
# handled separately so we only notify the dept_admin who actually owns the
# host's department.
_TENANT_WIDE_APPROVER_ROLES = ("receptionist", "super_admin")


async def _resolve_host_department_id(host_employee_id: Optional[str]) -> Optional[str]:
    """Look up the host system_user and return their ``department_id``.

    Returns ``None`` when no host is supplied, the id is malformed, the host
    can't be found, or the host has no department on file.
    """
    if not host_employee_id:
        return None
    if not ObjectId.is_valid(host_employee_id):
        return None
    from repositories.system_user_repo import get_system_user

    try:
        host = await get_system_user({"_id": ObjectId(host_employee_id)})
    except Exception:
        return None
    if host is None:
        return None
    return getattr(host, "department_id", None)


async def _get_active_checkin_approvers(
    tenant_id: str, host_employee_id: Optional[str] = None
) -> list:
    """Return every active system user who should be notified about a
    check-in for the tenant.

    ``receptionist`` and ``super_admin`` are tenant-wide and always
    included. ``dept_admin`` is scoped to a single department, so we only
    include dept_admins whose ``department_id`` matches the host's
    department. When the host or their department can't be resolved we
    fall back to including every active dept_admin — better to over-notify
    than to silently drop the alert when the host link is missing.
    """
    from repositories.system_user_repo import get_system_users
    from schemas.imports import AccountStatus

    host_department_id = await _resolve_host_department_id(host_employee_id)

    tenant_wide_filter: dict = {
        "tenant_id": tenant_id,
        "role": {"$in": list(_TENANT_WIDE_APPROVER_ROLES)},
        "account_status": AccountStatus.ACTIVE.value,
    }
    dept_admin_filter: dict = {
        "tenant_id": tenant_id,
        "role": "dept_admin",
        "account_status": AccountStatus.ACTIVE.value,
    }
    if host_department_id:
        dept_admin_filter["department_id"] = host_department_id

    tenant_wide = await get_system_users(tenant_wide_filter)
    dept_admins = await get_system_users(dept_admin_filter)

    seen: set[str] = set()
    out: list = []
    for user in list(tenant_wide) + list(dept_admins):
        uid = user.id or ""
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(user)
    return out


async def notify_checkin_pending_approval(
    tenant_id: str,
    checkin_id: str,
    visitor_name: str,
    verified: bool,
    purpose: str,
    host_employee_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: notify every active approver (receptionist,
    super_admin, plus the dept_admin who owns the host's department)
    about a pending check-in."""
    try:
        approvers = await _get_active_checkin_approvers(
            tenant_id, host_employee_id=host_employee_id
        )
        for user in approvers:
            try:
                await send_notification(
                    user_id=user.id or "",
                    user_type="system_user",
                    title="Pending Check-In Approval",
                    body=f"{visitor_name} ({purpose}) is awaiting approval.",
                    type="info",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify approver {user.id}", exc_info=True
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
    """Fire-and-forget: notify every active approver about an approved
    check-in and badge issuance. dept_admins outside the host's
    department are excluded."""
    try:
        approvers = await _get_active_checkin_approvers(
            tenant_id, host_employee_id=host_employee_id
        )
        for user in approvers:
            try:
                await send_notification(
                    user_id=user.id or "",
                    user_type="system_user",
                    title="Check-In Approved",
                    body=f"Badge issued for {visitor_name}. Badge ID: {badge_id}",
                    type="success",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify approver {user.id}", exc_info=True
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
    """Fire-and-forget: notify every active approver about a rejected
    check-in. dept_admins outside the host's department are excluded."""
    try:
        approvers = await _get_active_checkin_approvers(
            tenant_id, host_employee_id=host_employee_id
        )
        for user in approvers:
            try:
                await send_notification(
                    user_id=user.id or "",
                    user_type="system_user",
                    title="Check-In Rejected",
                    body=f"Check-in for {visitor_name} was rejected. Reason: {reason}",
                    type="warning",
                    link=f"/app/checkins/{checkin_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    f"Failed to notify approver {user.id}", exc_info=True
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


# --- Support-case notifications ---


async def notify_support_case_opened(
    tenant_id: str,
    case_id: str,
    subject: str,
    support_tier: str,
) -> None:
    """Fire-and-forget: notify opener + application admins a new case was opened.

    Issue 2 fix: emit shell-correct URLs from the start. Application
    admins live in the ``/admin/*`` shell, so support case links must
    target ``/admin/support-cases/{id}`` — the previous
    ``/app/admin/support-cases/{id}`` value was a tenant-shell path and
    the frontend's route resolver had to rewrite it (sometimes producing
    ``/admin/admin/...`` double-prefix bugs). Emitting the right URL
    here removes the rewrite from the hot path.
    """
    try:
        from repositories.admin_repo import get_admins
        from schemas.imports import AccountStatus

        # In-app notification to all active admins regardless of tier.
        admins = await get_admins(
            {"account_status": AccountStatus.ACTIVE.value}, start=0, stop=200
        )
        for admin in admins:
            try:
                await send_notification(
                    user_id=admin.id or "",
                    user_type="admin",
                    title="New Support Case",
                    body=f"A tenant opened a support case: {subject}",
                    type="info",
                    link=f"/admin/support-cases/{case_id}",
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.warning(
                    "Failed to notify admin %s about new support case",
                    admin.id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Failed to dispatch support-case-opened notifications", exc_info=True
        )


async def notify_onboarding_submission_received(
    submission_id: str,
    organization_name: Optional[str],
    full_name: Optional[str],
    email: Optional[str],
) -> None:
    """Fire-and-forget: notify every active application admin that a new
    self-onboarding submission has landed and needs review."""
    try:
        from repositories.admin_repo import get_admins
        from schemas.imports import AccountStatus

        admins = await get_admins(
            {"account_status": AccountStatus.ACTIVE.value}, start=0, stop=200
        )
        org_label = organization_name or full_name or email or "an applicant"
        for admin in admins:
            try:
                await send_notification(
                    user_id=admin.id or "",
                    user_type="admin",
                    title="New Onboarding Submission",
                    body=f"{org_label} submitted a self-onboarding request.",
                    type="info",
                    # Issue 2 fix: admin shell route is
                    # /admin/tenants/onboarding/{id}. Previously emitted
                    # /app/admin/onboarding/{id}, which the frontend
                    # had to rewrite. Emit the canonical admin URL.
                    link=f"/admin/tenants/onboarding/{submission_id}",
                )
            except Exception:
                logger.warning(
                    "Failed to notify admin %s about new onboarding submission",
                    admin.id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Failed to dispatch onboarding-submission-received notifications",
            exc_info=True,
        )


async def notify_support_case_reply(
    case_id: str,
    recipient_user_id: str,
    recipient_user_type: str,
    tenant_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: tell the counterparty that a reply landed on their case."""
    if not recipient_user_id:
        return
    try:
        await send_notification(
            user_id=recipient_user_id,
            user_type=recipient_user_type,
            title="New reply on your support case",
            body="A new message was posted on a support case you're part of.",
            type="info",
            link=f"/app/support-cases/{case_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send support-case reply notification", exc_info=True)


async def notify_support_case_status_change(
    case_id: str,
    new_status: str,
    recipient_user_id: str,
    recipient_user_type: str,
    tenant_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: tell the opener that case status has changed."""
    if not recipient_user_id:
        return
    try:
        await send_notification(
            user_id=recipient_user_id,
            user_type=recipient_user_type,
            title="Support case status updated",
            body=f"Your support case is now '{new_status}'.",
            type="info",
            link=f"/app/support-cases/{case_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("Failed to send support-case status notification", exc_info=True)


async def notify_support_case_assigned(
    case_id: str,
    admin_id: str,
    tenant_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: ping the admin that has just been assigned a case.

    Issue 2 fix: application admins live in the ``/admin/*`` shell.
    Previously emitted ``/app/admin/support-cases/{id}`` (tenant shell
    path) — corrected here to the canonical admin URL.
    """
    if not admin_id:
        return
    try:
        await send_notification(
            user_id=admin_id,
            user_type="admin",
            title="Support case assigned to you",
            body="You've been assigned a new support case.",
            type="info",
            link=f"/admin/support-cases/{case_id}",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning(
            "Failed to send support-case assigned notification", exc_info=True
        )


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


# ── Bucket summary (Issue 2) ───────────────────────────────────────


# Bucket name → list of substring patterns that classify a notification
# link as belonging to that bucket. Mirrors `resolveNotificationBucket`
# on the frontend; keep them in sync when adding a new bucket.
_BUCKET_PATTERNS: list[tuple[str, list[str]]] = [
    ("support_cases", ["/support-cases"]),
    (
        "onboarding_queue",
        ["/tenants/onboarding", "/admin/onboarding"],
    ),
    ("visitors", ["/visitors", "/checkins"]),
    ("appointments", ["/appointments"]),
    ("incidents", ["/incidents"]),
    ("jobs", ["/jobs"]),
    ("plans", ["/plans"]),
    ("pricing", ["/pricing"]),
    ("billing", ["/billing", "/subscriptions"]),
    ("content", ["/blogs", "/media", "/content"]),
]


def _classify_link_to_bucket(link: Optional[str]) -> Optional[str]:
    """Return the bucket name for a notification link, or ``None``."""
    if not link:
        return None
    for bucket, patterns in _BUCKET_PATTERNS:
        for pattern in patterns:
            if pattern in link:
                return bucket
    return None


async def get_notification_bucket_summary(
    *,
    user_id: str,
    user_type: str,
) -> dict[str, int]:
    """Aggregate unread notifications by sidebar bucket (Issue 2).

    Powers ``GET /v1/notifications/summary``. The frontend sidebar
    renders a numeric badge per bucket plus a pulsing dot when the
    collapsed rail has any non-zero bucket.

    Returns a dict keyed by bucket name with non-negative integer
    counts. Buckets with zero unread items are omitted to keep the
    payload small. Anything that doesn't match a known bucket pattern
    is dropped — the frontend has its own ``Other`` rendering path
    so we don't bother carrying noise across the wire.

    Implementation note: this iterates the user's unread notifications
    rather than running a separate count per bucket. With the default
    paging plus aggressive read-marking the typical unread set is
    small (<100 rows), so a single find + classify is faster than
    N MongoDB round trips. If a tenant ends up with thousands of
    unread items we should add a server-side classification field on
    the document and aggregate via ``$group``.
    """
    filter_dict: dict = {
        "user_id": user_id,
        "user_type": user_type,
        "read": False,
    }
    # Cap the scan at 1000 — way above any plausible UI need but
    # bounded so a misconfigured tenant can't OOM us.
    notifications = await get_notifications(filter_dict, skip=0, limit=1000)

    counts: dict[str, int] = {}
    for notif in notifications:
        bucket = _classify_link_to_bucket(notif.link)
        if not bucket:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


# ── Test notifications (Issue 6) ───────────────────────────────────


async def send_test_notification(
    *,
    user_id: str,
    user_type: str,
    tenant_id: Optional[str] = None,
) -> dict:
    """Send the diagnostic test notification (Issue 6).

    Drives the ``POST /v1/notifications/test`` endpoint the frontend
    notification-preferences pane + email-diagnostics card call.

    Behavior:
      - Always creates an in-app notification (so the admin sees the
        bell-icon update even when SMTP is offline).
      - Then tries to send an email via the mounted
        ``notification_test`` template. Three short-circuits each
        return a structured ``skipped_reason`` rather than raising,
        so the frontend can render an actionable status line:

          - ``email_disabled_in_preferences`` — user toggled the
            master switch off.
          - ``smtp_not_configured`` — platform SMTP isn't wired up
            (``EMAIL_HOST``/etc. missing in settings).
          - ``missing_recipient_email`` — no email on file for this
            account (defensive guard; shouldn't happen in practice).

    Return shape (consumed by the frontend's ``useSendTestNotification``
    hook):

    ``{ "delivered": bool, "skipped_reason": str | None,
        "message": str | None }``
    """
    from core.email.manager import EmailManager
    from core.email.types import EmailDispatchRequest
    from core.settings import get_settings
    from datetime import datetime, timezone

    # Always drop an in-app notification so the bell pulses even on
    # SMTP failure. Use a fixed title/body so the row is recognisable.
    try:
        await send_notification(
            user_id=user_id,
            user_type=user_type,
            title="Notification settings test",
            body="If you also received this as an email, your delivery pipeline is fully configured.",
            type="info",
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning(
            "send_test_notification: in-app delivery failed for user_id=%s",
            user_id,
            exc_info=True,
        )

    # Resolve email + preferences for the master toggle check.
    recipient_email: Optional[str] = None
    recipient_name: Optional[str] = None
    email_master_enabled = True

    try:
        if user_type == "admin":
            from repositories.admin_repo import get_admin
            from bson import ObjectId

            if ObjectId.is_valid(user_id):
                admin = await get_admin({"_id": ObjectId(user_id)})
                if admin:
                    recipient_email = admin.email
                    recipient_name = admin.full_name
        elif user_type == "system_user":
            from repositories.system_user_repo import get_system_user
            from bson import ObjectId

            if ObjectId.is_valid(user_id):
                user = await get_system_user({"_id": ObjectId(user_id)})
                if user:
                    recipient_email = user.email
                    recipient_name = user.full_name
    except Exception:
        logger.warning(
            "send_test_notification: failed to resolve recipient email for %s/%s",
            user_type,
            user_id,
            exc_info=True,
        )

    try:
        from repositories.user_settings_repo import get_user_settings

        settings_row = await get_user_settings(
            {"user_id": user_id, "user_type": user_type}
        )
        if settings_row is not None:
            email_master_enabled = bool(
                getattr(settings_row, "email_notifications", True)
            )
    except Exception:
        # Missing settings is not fatal — fall back to "enabled" so a
        # fresh account can still receive its first test.
        pass

    if not email_master_enabled:
        return {
            "delivered": False,
            "skipped_reason": "email_disabled_in_preferences",
            "message": None,
        }

    if not recipient_email:
        return {
            "delivered": False,
            "skipped_reason": "missing_recipient_email",
            "message": None,
        }

    settings = get_settings()
    smtp_host = getattr(settings, "email_host", None)
    if not smtp_host:
        return {
            "delivered": False,
            "skipped_reason": "smtp_not_configured",
            "message": None,
        }

    # Fire the email. Use the queue-aware dispatch so production
    # respects ``EMAIL_QUEUE_ENABLED`` (background send via Celery)
    # while local dev sends synchronously.
    try:
        from_email = (
            getattr(settings, "email_from_email", None)
            or getattr(settings, "email_username", None)
        )
        manager = EmailManager.get_instance()
        result = await manager.send_template(
            EmailDispatchRequest(
                to_email=recipient_email,
                template_key="notification_test",
                context={
                    "recipient_name": recipient_name or recipient_email,
                    "platform_name": getattr(
                        settings, "platform_name", "VisiChek"
                    ),
                    "triggered_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    + "Z",
                    "from_address": from_email or "",
                },
                dispatch="auto",
            )
        )
        # `result.status` is one of "sent" | "queued" | "failed". Both
        # "sent" and "queued" represent a successful handoff — the
        # frontend renders "Test sent — check your inbox shortly" in
        # both cases.
        delivered = result.status in ("sent", "queued")
        return {
            "delivered": delivered,
            "skipped_reason": None if delivered else "send_failed",
            "message": None,
        }
    except RuntimeError as exc:
        # Raised when SMTP retries are exhausted or the transport is
        # missing despite host being set. Surface the message so the
        # admin can fix it.
        logger.warning(
            "send_test_notification: SMTP send failed for %s: %s",
            recipient_email,
            exc,
        )
        return {
            "delivered": False,
            "skipped_reason": "smtp_send_failed",
            "message": str(exc),
        }
    except Exception as exc:
        logger.warning(
            "send_test_notification: unexpected error for %s",
            recipient_email,
            exc_info=True,
        )
        return {
            "delivered": False,
            "skipped_reason": "unexpected_error",
            "message": str(exc),
        }
