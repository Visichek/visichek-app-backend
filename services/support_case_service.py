"""Support-case service: state machine, tier resolution, notifications.

"Support cases" are long-running support threads between a tenant and the
application admins. Naming note: this module is NOT the tenant-internal
NDPC incident log (see ``services/incident_service.py``). They're different
domains that happen to share the colloquial name "incident".

Responsibilities:
  * Enforce the 10-open-case cap per tenant (plan-independent).
  * Enforce the state-machine transitions and actor permissions.
  * Resolve the effective ``support_tier`` for a tenant via the cached plan.
  * Queue tenant emails on every state change (always).
  * Queue admin emails gated by the tenant's support tier.
  * Emit in-app notifications (tier-independent).
  * Write an audit trail and invalidate precomputed list caches.
  * Expose cron entry points for auto-close (RESOLVED→CLOSED after 7d) and
    for nudging tenants whose AWAITING_TENANT cases have gone quiet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.queue.precompute import PrecomputeScope, register_precompute
from core.redis_cache import cache_db
from repositories.support_case_message_repo import (
    create_message,
    list_messages_for_case,
)
from repositories.support_case_repo import (
    count_open_cases_for_tenant,
    create_support_case,
    get_support_case_by_id,
    increment_message_count,
    list_awaiting_tenant_stale_cases,
    list_resolved_stale_cases,
    list_sla_breached_cases,
    list_support_cases,
    update_support_case,
)
from schemas.imports import (
    SupportCaseAuthorType,
    SupportCaseStatus,
    SupportCasePriority,
    SupportTier,
    UserType,
)
from schemas.support_case_schema import (
    OPEN_STATUSES,
    SLA_WINDOWS_SECONDS,
    SupportCaseAttachment,
    SupportCaseCreate,
    SupportCaseMessageCreate,
    SupportCaseMessageOut,
    SupportCaseMessageWithSummaryOut,
    SupportCaseOut,
    SupportCaseUpdate,
    SupportCaseWithSummaryOut,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

# --- Configuration constants ---

MAX_OPEN_CASES_PER_TENANT = 10

# RESOLVED cases auto-close after 7d of inactivity.
RESOLVED_AUTO_CLOSE_AFTER_SECONDS = 7 * 24 * 3600

# AWAITING_TENANT cases get a nudge email after 48h of tenant silence.
AWAITING_TENANT_NUDGE_AFTER_SECONDS = 48 * 3600

# Admin-reply email throttle window — multiple admin messages within this
# window collapse into a single tenant notification email.
ADMIN_REPLY_THROTTLE_SECONDS = 60


# --- Allowed transitions: (from, to) -> set of actor_types that may trigger ---

_ALLOWED_TRANSITIONS: dict[tuple[str, str], set[str]] = {
    (SupportCaseStatus.OPEN.value, SupportCaseStatus.ACKNOWLEDGED.value): {"admin"},
    (SupportCaseStatus.ACKNOWLEDGED.value, SupportCaseStatus.IN_PROGRESS.value): {
        "admin"
    },
    (SupportCaseStatus.IN_PROGRESS.value, SupportCaseStatus.AWAITING_TENANT.value): {
        "admin"
    },
    (SupportCaseStatus.AWAITING_TENANT.value, SupportCaseStatus.IN_PROGRESS.value): {
        "admin",
        "tenant",
        "system",
    },
    (SupportCaseStatus.IN_PROGRESS.value, SupportCaseStatus.RESOLVED.value): {"admin"},
    (SupportCaseStatus.RESOLVED.value, SupportCaseStatus.CLOSED.value): {
        "tenant",
        "system",
    },
    (SupportCaseStatus.RESOLVED.value, SupportCaseStatus.REOPENED.value): {"tenant"},
    (SupportCaseStatus.REOPENED.value, SupportCaseStatus.IN_PROGRESS.value): {"admin"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor_type(role: Optional[str]) -> str:
    from security.principal import TENANT_USER_ROLES

    if role == "admin":
        return "admin"
    if role and role in TENANT_USER_ROLES:
        return "tenant"
    return "system"


async def _resolve_support_tier(tenant_id: str) -> SupportTier:
    """Read the effective support tier for a tenant.

    Falls back to NONE when the tenant has no active subscription.
    """
    from services.plan_cache_service import resolve_tenant_plan

    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return SupportTier.NONE
    tier_raw = plan_data.get("support_tier", "none") or "none"
    try:
        return SupportTier(tier_raw)
    except ValueError:
        return SupportTier.NONE


async def _resolve_tenant_company_name(tenant_id: str) -> str:
    try:
        from repositories.tenant_repo import get_tenant

        tenant = await get_tenant({"_id": ObjectId(tenant_id)})
        if tenant and getattr(tenant, "company_name", None):
            return tenant.company_name
    except Exception:
        logger.debug("resolve tenant company name failed", exc_info=True)
    return "Your organization"


async def _resolve_tenant_opener_email(case: SupportCaseOut) -> Optional[str]:
    """Best-effort lookup for the tenant user who opened the case."""
    if not case.opened_by:
        return None
    try:
        from repositories.system_user_repo import get_system_user

        user = await get_system_user({"_id": ObjectId(case.opened_by)})
        if user and getattr(user, "email", None):
            return str(user.email)
    except Exception:
        logger.debug("resolve tenant opener email failed", exc_info=True)
    return None


async def _list_admin_recipients() -> List[tuple[str, str]]:
    """Return ``(id, email)`` of every active application admin.

    The id rides along so email dispatch can honor each admin's own
    notification preferences.
    """
    try:
        from repositories.admin_repo import get_admins
        from schemas.imports import AccountStatus

        admins = await get_admins(
            {"account_status": AccountStatus.ACTIVE.value}, start=0, stop=200
        )
        return [
            (str(a.id or ""), str(a.email)) for a in admins if getattr(a, "email", None)
        ]
    except Exception:
        logger.debug("list admin emails failed", exc_info=True)
        return []


async def _resolve_admin_email(admin_id: str) -> Optional[str]:
    try:
        from repositories.admin_repo import get_admin

        admin = await get_admin({"_id": ObjectId(admin_id)})
        if admin and getattr(admin, "email", None):
            return str(admin.email)
    except Exception:
        logger.debug("resolve admin email failed", exc_info=True)
    return None


def _invalidate_tenant_cache_sync(tenant_id: str) -> None:
    """Drop the precomputed list entry for a tenant so the next reader recomputes."""
    try:
        cache_db.delete(f"precomputed:tenant:{tenant_id}:support_cases.list")
    except Exception:
        logger.debug("invalidate tenant cache failed", exc_info=True)


def _invalidate_admin_cache_sync() -> None:
    try:
        cache_db.delete("precomputed:global:support_cases.admin_list")
    except Exception:
        logger.debug("invalidate admin cache failed", exc_info=True)


def _enqueue_list_refresh(tenant_id: str) -> None:
    """Drop the cache AND ask the precompute queue to rebuild it."""
    _invalidate_tenant_cache_sync(tenant_id)
    _invalidate_admin_cache_sync()
    try:
        from core.queue.manager import QueueManager

        qm = QueueManager.get_instance()
        qm.enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "support_cases.list"},
        )
        qm.enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": "", "resource": "support_cases.admin_list"},
        )
    except Exception:
        logger.debug("support_case cache refresh enqueue failed", exc_info=True)


def _compute_sla_due_at(priority: SupportCasePriority, date_created: int) -> int:
    window = SLA_WINDOWS_SECONDS.get(
        priority.value, SLA_WINDOWS_SECONDS[SupportCasePriority.MEDIUM.value]
    )
    return date_created + window


def _throttle_admin_reply_email(case_id: str) -> bool:
    """Decide whether to send an ``admin_replied`` email to the tenant.

    Returns True if we should send. Stores a short-lived Redis key so further
    admin messages within ``ADMIN_REPLY_THROTTLE_SECONDS`` collapse into one
    email.
    """
    key = f"support_case:email_throttle:{case_id}:tenant"
    try:
        acquired = cache_db.set(key, "1", nx=True, ex=ADMIN_REPLY_THROTTLE_SECONDS)
        return bool(acquired)
    except Exception:
        return True  # when Redis is flaky, err towards delivery


async def _email_pref_allows(user_id: str, user_type: "UserType") -> bool:
    """Whether the recipient's preferences allow a support-case email.

    Mirrors the notification fan-out gate: master ``email_notifications``
    in user settings, then ``email_enabled`` + ``email_on_support_case``
    in notification preferences. Historically support-case emails ignored
    these toggles entirely, so the ``email_on_support_case`` switch in the
    UI did nothing. Fails OPEN on lookup errors — a Redis/Mongo blip must
    never silently drop a support email.
    """
    try:
        from repositories.user_settings_repo import get_user_settings

        settings_row = await get_user_settings(
            {"user_id": user_id, "user_type": user_type}
        )
        if settings_row is not None and not bool(
            getattr(settings_row, "email_notifications", True)
        ):
            return False
    except Exception:
        pass
    try:
        from services.notification_service import (
            retrieve_or_create_notification_preferences,
        )

        prefs = await retrieve_or_create_notification_preferences(user_id, user_type)
        if not bool(getattr(prefs, "email_enabled", True)):
            return False
        if not bool(getattr(prefs, "email_on_support_case", True)):
            return False
    except Exception:
        pass
    return True


async def _queue_email(
    to_email: str,
    template_key: str,
    context: dict[str, Any],
    *,
    recipient_user_id: Optional[str] = None,
    recipient_user_type: Optional["UserType"] = None,
) -> None:
    """Queue a templated email — silent on failure.

    When the caller knows who the recipient is (``recipient_user_id`` +
    ``recipient_user_type``), the send respects that user's email
    preferences (master toggle + ``email_on_support_case``).
    """
    if not to_email:
        return
    if recipient_user_id and recipient_user_type is not None:
        if not await _email_pref_allows(recipient_user_id, recipient_user_type):
            logger.info(
                "support_case email skipped (preferences) template=%s user=%s",
                template_key,
                recipient_user_id,
            )
            return
    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=to_email,
                template_key=template_key,
                context=context,
                dispatch="queued",
            )
        )
    except Exception:
        logger.warning(
            "support_case email dispatch failed template=%s to=%s",
            template_key,
            to_email,
            exc_info=True,
        )


def _case_context(
    case: SupportCaseOut,
    *,
    company_name: str,
    support_tier: SupportTier,
    actor_name: Optional[str] = None,
    message_preview: Optional[str] = None,
) -> dict[str, Any]:
    case_url = f"/app/support-cases/{case.id}" if case.id else "/app/support-cases"
    return {
        "case_id": case.id or "",
        "case_subject": case.subject or "",
        "case_status": (
            case.status.value if hasattr(case.status, "value") else str(case.status)
        )
        if case.status is not None
        else "",
        "case_priority": (
            case.priority.value
            if hasattr(case.priority, "value")
            else str(case.priority)
        ),
        "case_category": (
            case.category.value
            if hasattr(case.category, "value")
            else str(case.category)
        ),
        "tenant_company_name": company_name,
        "tenant_id": case.tenant_id or "",
        "actor_name": actor_name or "A VisiChek team member",
        "message_preview": (message_preview or "")[:200],
        "case_url": case_url,
        "support_tier": support_tier.value,
    }


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


async def _enforce_open_case_cap(tenant_id: str) -> None:
    current = await count_open_cases_for_tenant(tenant_id)
    if current >= MAX_OPEN_CASES_PER_TENANT:
        # Audit the hard rejection so we can spot tenants chronically at the wall.
        try:
            await record_audit_event(
                actor_id=tenant_id,
                actor_role="tenant",
                action="support_case.cap_exceeded",
                resource_type="support_case",
                resource_id="",
                tenant_id=tenant_id,
                details={"open_count": current, "cap": MAX_OPEN_CASES_PER_TENANT},
            )
        except Exception:
            pass
        raise AppException(
            status_code=429,
            code=ErrorCode.QUOTA_EXCEEDED,
            message=(
                f"Tenant has reached the maximum of {MAX_OPEN_CASES_PER_TENANT} "
                "open support cases. Resolve or close existing cases before "
                "opening a new one."
            ),
            details={"open_count": current, "cap": MAX_OPEN_CASES_PER_TENANT},
        )


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------


def _validate_transition(current: str, target: str, actor_type: str) -> None:
    if current == target:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Case is already in status '{current}'",
        )
    if current == SupportCaseStatus.CLOSED.value:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Closed cases cannot transition further",
        )
    allowed_actors = _ALLOWED_TRANSITIONS.get((current, target))
    if allowed_actors is None:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Illegal transition: {current} → {target}",
        )
    if actor_type not in allowed_actors:
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message=(
                f"Actor type '{actor_type}' cannot transition a case from "
                f"{current} → {target}"
            ),
        )


# ---------------------------------------------------------------------------
# Public API — case lifecycle
# ---------------------------------------------------------------------------


async def add_support_case(
    *,
    subject: str,
    description: str,
    category: str,
    priority: str,
    tenant_id: str,
    opened_by: str,
    opened_by_role: str,
    preassigned_id: Optional[str] = None,
) -> SupportCaseOut:
    """Create a new case, emit notifications + emails per the tenant's tier."""
    # 1. Hard cap (race-safe re-check; the route also does a fast sync check)
    await _enforce_open_case_cap(tenant_id)

    try:
        priority_enum = SupportCasePriority(priority)
    except ValueError:
        priority_enum = SupportCasePriority.MEDIUM

    from schemas.imports import SupportCaseCategory

    try:
        category_enum = SupportCaseCategory(category)
    except ValueError:
        category_enum = SupportCaseCategory.OTHER

    payload = SupportCaseCreate(
        subject=subject,
        description=description,
        category=category_enum,
        priority=priority_enum,
        tenant_id=tenant_id,
        opened_by=opened_by,
        opened_by_role=opened_by_role,
    )

    created = await create_support_case(payload, preassigned_id=preassigned_id)

    # 2. Resolve tenant tier + context
    support_tier = await _resolve_support_tier(tenant_id)
    company_name = await _resolve_tenant_company_name(tenant_id)
    opener_email = await _resolve_tenant_opener_email(created)
    ctx = _case_context(
        created,
        company_name=company_name,
        support_tier=support_tier,
        message_preview=description,
    )

    # 3. Emails: tenant always, admins only for STANDARD+
    if opener_email:
        await _queue_email(
            opener_email,
            "support_case.opened.tenant",
            ctx,
            recipient_user_id=created.opened_by,
            recipient_user_type=UserType.SYSTEM_USER,
        )

    if support_tier in (SupportTier.STANDARD, SupportTier.PRIORITY):
        for admin_id, email in await _list_admin_recipients():
            await _queue_email(
                email,
                "support_case.opened.admin",
                ctx,
                recipient_user_id=admin_id or None,
                recipient_user_type=UserType.ADMIN,
            )

    # 4. In-app notifications: tier-independent
    try:
        from services.notification_service import (
            notify_support_case_opened,
        )

        await notify_support_case_opened(
            tenant_id=tenant_id,
            case_id=created.id or "",
            subject=created.subject,
            support_tier=support_tier.value,
        )
    except Exception:
        logger.debug("in-app notify on open failed", exc_info=True)

    # 5. Audit
    try:
        await record_audit_event(
            actor_id=opened_by,
            actor_role=opened_by_role,
            action="support_case.created",
            resource_type="support_case",
            resource_id=created.id or "",
            tenant_id=tenant_id,
            details={
                "subject": created.subject,
                "priority": priority_enum.value,
                "category": category_enum.value,
                "support_tier": support_tier.value,
            },
        )
    except Exception:
        pass

    _enqueue_list_refresh(tenant_id)
    return created


async def add_support_case_message(
    *,
    case_id: str,
    author_id: str,
    author_role: str,
    body: str,
    attachments: Optional[List[dict]] = None,
    internal_note: bool = False,
    preassigned_id: Optional[str] = None,
) -> SupportCaseMessageOut:
    """Append a message to a case thread, with auto-transition + notifications."""
    case = await get_support_case_by_id(case_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    if case.status == SupportCaseStatus.CLOSED:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Cannot post messages on a closed case",
        )

    actor_type = _actor_type(author_role)
    # Only admins may write internal notes.
    if internal_note and actor_type != "admin":
        internal_note = False

    atts: List[SupportCaseAttachment] = []
    for raw in attachments or []:
        try:
            atts.append(SupportCaseAttachment(**raw))
        except Exception:
            logger.warning("invalid attachment payload dropped", extra={"raw": raw})

    message_payload = SupportCaseMessageCreate(
        case_id=case_id,
        author_id=author_id,
        author_role=author_role,
        author_type=SupportCaseAuthorType(actor_type),
        body=body,
        internal_note=internal_note,
        attachments=atts,
    )
    msg = await create_message(message_payload, preassigned_id=preassigned_id)

    # Update counters / last_message_at
    await increment_message_count(case_id, attachments_added=len(atts))

    # Auto-flip AWAITING_TENANT → IN_PROGRESS when the tenant replies.
    if actor_type == "tenant" and case.status == SupportCaseStatus.AWAITING_TENANT:
        await update_support_case(
            case_id,
            SupportCaseUpdate(status=SupportCaseStatus.IN_PROGRESS),
        )
        case = await get_support_case_by_id(case_id) or case

    tenant_id = case.tenant_id or ""
    support_tier = await _resolve_support_tier(tenant_id)
    company_name = await _resolve_tenant_company_name(tenant_id)
    ctx = _case_context(
        case,
        company_name=company_name,
        support_tier=support_tier,
        message_preview=body,
    )

    # Email fan-out (internal notes never trigger tenant emails)
    if not internal_note:
        if actor_type == "admin":
            opener_email = await _resolve_tenant_opener_email(case)
            if opener_email and _throttle_admin_reply_email(case_id):
                await _queue_email(
                    opener_email,
                    "support_case.admin_replied.tenant",
                    ctx,
                    recipient_user_id=case.opened_by,
                    recipient_user_type=UserType.SYSTEM_USER,
                )
        elif actor_type == "tenant":
            if support_tier == SupportTier.PRIORITY and case.assigned_admin_id:
                admin_email = await _resolve_admin_email(case.assigned_admin_id)
                if admin_email:
                    await _queue_email(
                        admin_email,
                        "support_case.tenant_replied.admin",
                        ctx,
                        recipient_user_id=case.assigned_admin_id,
                        recipient_user_type=UserType.ADMIN,
                    )

    # In-app notifications
    try:
        from services.notification_service import notify_support_case_reply

        if actor_type == "admin":
            # Notify the tenant opener
            await notify_support_case_reply(
                case_id=case_id,
                recipient_user_id=case.opened_by or "",
                recipient_user_type=UserType.SYSTEM_USER,
                tenant_id=tenant_id,
            )
        elif actor_type == "tenant" and case.assigned_admin_id:
            await notify_support_case_reply(
                case_id=case_id,
                recipient_user_id=case.assigned_admin_id,
                recipient_user_type=UserType.ADMIN,
                tenant_id=tenant_id,
            )
    except Exception:
        logger.debug("in-app notify on reply failed", exc_info=True)

    try:
        await record_audit_event(
            actor_id=author_id,
            actor_role=author_role,
            action="support_case.message_added",
            resource_type="support_case",
            resource_id=case_id,
            tenant_id=tenant_id,
            details={
                "internal_note": internal_note,
                "attachments": len(atts),
                "author_type": actor_type,
            },
        )
    except Exception:
        pass

    _enqueue_list_refresh(tenant_id)
    return msg


async def transition_support_case(
    *,
    case_id: str,
    new_status: str,
    actor_id: str,
    actor_role: str,
) -> SupportCaseOut:
    case = await get_support_case_by_id(case_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    try:
        target_enum = SupportCaseStatus(new_status)
    except ValueError:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Unknown status: {new_status}",
        )

    current_value = (
        case.status.value if case.status is not None else SupportCaseStatus.OPEN.value
    )
    actor_type = _actor_type(actor_role)
    _validate_transition(current_value, target_enum.value, actor_type)

    update = SupportCaseUpdate(status=target_enum)
    now = int(time.time())
    if target_enum == SupportCaseStatus.RESOLVED:
        update.resolved_at = now
    if target_enum == SupportCaseStatus.CLOSED:
        update.closed_at = now

    updated = await update_support_case(case_id, update)
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to update support case",
        )

    tenant_id = updated.tenant_id or ""
    support_tier = await _resolve_support_tier(tenant_id)
    company_name = await _resolve_tenant_company_name(tenant_id)
    opener_email = await _resolve_tenant_opener_email(updated)
    ctx = _case_context(updated, company_name=company_name, support_tier=support_tier)

    # Tenant emails — always
    tenant_template = {
        SupportCaseStatus.ACKNOWLEDGED.value: "support_case.acknowledged.tenant",
        SupportCaseStatus.AWAITING_TENANT.value: "support_case.awaiting_tenant",
        SupportCaseStatus.RESOLVED.value: "support_case.resolved.tenant",
        SupportCaseStatus.CLOSED.value: "support_case.closed.tenant",
    }.get(target_enum.value)
    if opener_email and tenant_template:
        await _queue_email(
            opener_email,
            tenant_template,
            ctx,
            recipient_user_id=case.opened_by,
            recipient_user_type=UserType.SYSTEM_USER,
        )

    # Admin emails — tier-gated for PRIORITY (per-event notifications)
    if support_tier == SupportTier.PRIORITY and case.assigned_admin_id:
        admin_email = await _resolve_admin_email(case.assigned_admin_id)
        if admin_email and target_enum in (
            SupportCaseStatus.REOPENED,
            SupportCaseStatus.CLOSED,
        ):
            # Re-use the generic assigned template since these are admin-side pings.
            await _queue_email(
                admin_email,
                "support_case.assigned.admin",
                ctx,
                recipient_user_id=case.assigned_admin_id,
                recipient_user_type=UserType.ADMIN,
            )

    # In-app
    try:
        from services.notification_service import (
            notify_support_case_status_change,
        )

        await notify_support_case_status_change(
            case_id=case_id,
            new_status=target_enum.value,
            recipient_user_id=updated.opened_by or "",
            recipient_user_type=UserType.SYSTEM_USER,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.debug("in-app notify on transition failed", exc_info=True)

    try:
        action = (
            "support_case.closed"
            if target_enum == SupportCaseStatus.CLOSED
            else "support_case.reopened"
            if target_enum == SupportCaseStatus.REOPENED
            else "support_case.transitioned"
        )
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            resource_type="support_case",
            resource_id=case_id,
            tenant_id=tenant_id,
            details={"from": current_value, "to": target_enum.value},
        )
    except Exception:
        pass

    _enqueue_list_refresh(tenant_id)
    return updated


async def assign_support_case(
    *,
    case_id: str,
    admin_id: str,
    actor_id: str,
    actor_role: str,
) -> SupportCaseOut:
    case = await get_support_case_by_id(case_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    updated = await update_support_case(
        case_id, SupportCaseUpdate(assigned_admin_id=admin_id)
    )
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to assign support case",
        )

    tenant_id = updated.tenant_id or ""
    support_tier = await _resolve_support_tier(tenant_id)
    if support_tier == SupportTier.PRIORITY:
        admin_email = await _resolve_admin_email(admin_id)
        if admin_email:
            company_name = await _resolve_tenant_company_name(tenant_id)
            ctx = _case_context(
                updated,
                company_name=company_name,
                support_tier=support_tier,
            )
            await _queue_email(
                admin_email,
                "support_case.assigned.admin",
                ctx,
                recipient_user_id=admin_id,
                recipient_user_type=UserType.ADMIN,
            )

    try:
        from services.notification_service import notify_support_case_assigned

        await notify_support_case_assigned(
            case_id=case_id, admin_id=admin_id, tenant_id=tenant_id
        )
    except Exception:
        logger.debug("in-app notify on assign failed", exc_info=True)

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="support_case.assigned",
            resource_type="support_case",
            resource_id=case_id,
            tenant_id=tenant_id,
            details={"admin_id": admin_id},
        )
    except Exception:
        pass

    _enqueue_list_refresh(tenant_id)
    return updated


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


async def _enrich_support_case(case: SupportCaseOut) -> SupportCaseWithSummaryOut:
    from services.summary_resolver import (
        resolve_admin_summary,
        resolve_system_user_summary,
        resolve_tenant_summary,
        resolve_user_summary,
    )

    async def _resolve_assignee(assignee_id: Optional[str]):
        if not assignee_id:
            return None
        summary = await resolve_admin_summary(assignee_id)
        if summary is not None:
            return summary
        return await resolve_system_user_summary(assignee_id)

    tenant_s, opener_s, assigned_s = await asyncio.gather(
        resolve_tenant_summary(case.tenant_id),
        resolve_user_summary(
            case.opened_by,
            user_type=UserType.ADMIN
            if case.opened_by_role == "admin"
            else UserType.SYSTEM_USER,
        ),
        _resolve_assignee(case.assigned_admin_id),
    )
    data = case.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_s
    data["opened_by_summary"] = opener_s
    data["assigned_admin_summary"] = assigned_s
    return SupportCaseWithSummaryOut(**data)


async def _enrich_support_case_message(
    msg: SupportCaseMessageOut,
) -> SupportCaseMessageWithSummaryOut:
    from services.summary_resolver import resolve_user_summary

    author_type = (
        "admin"
        if msg.author_type is not None
        and (
            msg.author_type.value
            if hasattr(msg.author_type, "value")
            else str(msg.author_type)
        )
        == "admin"
        else "system_user"
    )
    author_s = await resolve_user_summary(msg.author_id, user_type=author_type)
    data = msg.model_dump(by_alias=False)
    data["author_summary"] = author_s
    return SupportCaseMessageWithSummaryOut(**data)


async def retrieve_support_case_by_id(
    case_id: str,
    *,
    tenant_id: Optional[str] = None,
    requester_role: str,
) -> dict[str, Any]:
    """Return case + messages, stripping internal notes for tenant readers."""
    case = await get_support_case_by_id(case_id, tenant_id=tenant_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    include_internal = requester_role == "admin"
    messages = await list_messages_for_case(
        case_id, include_internal=include_internal, start=0, stop=500
    )
    enriched_case, enriched_messages = await asyncio.gather(
        _enrich_support_case(case),
        asyncio.gather(*[_enrich_support_case_message(m) for m in messages]),
    )
    return {
        "case": enriched_case.model_dump(mode="json", by_alias=True),
        "messages": [
            m.model_dump(mode="json", by_alias=True) for m in enriched_messages
        ],
    }


async def enrich_support_case_dicts(
    docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-attach tenant/opener/assignee summaries to raw list docs.

    The unfiltered first page is served pre-enriched from the precompute
    cache (``_precompute_support_cases_admin_list``). The live ``run_list``
    path returns raw Mongo docs with only ``_id`` stringified, so the
    summary snapshots the frontend renders (company name, assignee name)
    would be missing the moment any filter/sort/search is applied. Enrich
    them here so both paths return an identical ``SupportCaseWithSummaryOut``
    shape. Re-resolving per request is cheap: the page is capped at the
    list limit (<= 200) and the summary resolvers are individually cached.
    """
    if not docs:
        return []
    cases = [SupportCaseOut(**d) for d in docs]
    enriched = await asyncio.gather(*[_enrich_support_case(c) for c in cases])
    return [e.model_dump(mode="json", by_alias=True) for e in enriched]


async def retrieve_support_cases(
    *,
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assigned_admin_id: Optional[str] = None,
    support_tier: Optional[str] = None,
    start: int = 0,
    stop: int = 100,
) -> List[SupportCaseWithSummaryOut]:
    cases = await list_support_cases(
        tenant_id=tenant_id,
        status=status,
        priority=priority,
        category=category,
        assigned_admin_id=assigned_admin_id,
        start=start,
        stop=stop,
    )
    if support_tier:
        # Post-filter: resolve tier per tenant.
        tenant_tiers: dict[str, str] = {}
        filtered: List[SupportCaseOut] = []
        for c in cases:
            tid = c.tenant_id or ""
            if tid not in tenant_tiers:
                t = await _resolve_support_tier(tid)
                tenant_tiers[tid] = t.value
            if tenant_tiers[tid] == support_tier:
                filtered.append(c)
        cases = filtered
    return list(await asyncio.gather(*[_enrich_support_case(c) for c in cases]))


async def retrieve_messages_for_case(
    case_id: str,
    *,
    requester_role: str,
    start: int = 0,
    stop: int = 200,
) -> List[SupportCaseMessageWithSummaryOut]:
    messages = await list_messages_for_case(
        case_id,
        include_internal=(requester_role == "admin"),
        start=start,
        stop=stop,
    )
    return list(
        await asyncio.gather(*[_enrich_support_case_message(m) for m in messages])
    )


async def retrieve_cases_approaching_sla(
    *, start: int = 0, stop: int = 100
) -> List[SupportCaseOut]:
    """Active cases whose SLA will elapse within 24 hours."""
    now = int(time.time())
    deadline = now + 24 * 3600

    from core.database import db

    cursor = (
        db["support_cases"]
        .find(
            {
                "status": {"$in": list(OPEN_STATUSES)},
                "sla_due_at": {"$lte": deadline, "$gte": now},
            }
        )
        .sort("sla_due_at", 1)
        .skip(start)
        .limit(max(stop - start, 0))
    )
    return [SupportCaseOut(**doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Cron entry points (referenced by APScheduler via textual paths)
# ---------------------------------------------------------------------------


async def auto_close_resolved_cases() -> None:
    """Close any RESOLVED case that has been inactive for 7 days."""
    now = int(time.time())
    threshold = now - RESOLVED_AUTO_CLOSE_AFTER_SECONDS
    try:
        stale = await list_resolved_stale_cases(threshold)
    except Exception:
        logger.warning("auto_close_resolved_cases: list failed", exc_info=True)
        return
    for case in stale:
        try:
            await transition_support_case(
                case_id=case.id or "",
                new_status=SupportCaseStatus.CLOSED.value,
                actor_id="system",
                actor_role="system",
            )
        except Exception:
            logger.warning(
                "auto_close_resolved_cases: failed for %s",
                case.id,
                exc_info=True,
            )


async def nudge_awaiting_tenant_cases() -> None:
    """Nudge tenants whose cases have been AWAITING_TENANT for 48h."""
    now = int(time.time())
    threshold = now - AWAITING_TENANT_NUDGE_AFTER_SECONDS
    try:
        stale = await list_awaiting_tenant_stale_cases(threshold)
    except Exception:
        logger.warning("nudge_awaiting_tenant_cases: list failed", exc_info=True)
        return
    for case in stale:
        nudge_key = f"support_case:awaiting_nudge:{case.id}"
        try:
            if not cache_db.set(nudge_key, "1", nx=True, ex=24 * 3600):
                continue  # already nudged within the last 24h
        except Exception:
            pass
        tenant_id = case.tenant_id or ""
        try:
            opener_email = await _resolve_tenant_opener_email(case)
            support_tier = await _resolve_support_tier(tenant_id)
            company_name = await _resolve_tenant_company_name(tenant_id)
            ctx = _case_context(
                case, company_name=company_name, support_tier=support_tier
            )
            if opener_email:
                await _queue_email(
                    opener_email,
                    "support_case.awaiting_tenant",
                    ctx,
                    recipient_user_id=case.opened_by,
                    recipient_user_type=UserType.SYSTEM_USER,
                )
        except Exception:
            logger.warning("nudge email dispatch failed", exc_info=True)


async def alert_sla_breaches() -> None:
    """Admin-side SLA breach alerts for STANDARD+ tenants."""
    now = int(time.time())
    try:
        breached = await list_sla_breached_cases(now)
    except Exception:
        logger.warning("alert_sla_breaches: list failed", exc_info=True)
        return
    for case in breached:
        breach_key = f"support_case:sla_breach:{case.id}"
        try:
            if not cache_db.set(breach_key, "1", nx=True, ex=6 * 3600):
                continue  # don't re-page more than once per 6h per case
        except Exception:
            pass
        tenant_id = case.tenant_id or ""
        support_tier = await _resolve_support_tier(tenant_id)
        if support_tier == SupportTier.NONE:
            continue
        company_name = await _resolve_tenant_company_name(tenant_id)
        ctx = _case_context(case, company_name=company_name, support_tier=support_tier)
        recipients: List[tuple[str, str]] = []
        if case.assigned_admin_id:
            email = await _resolve_admin_email(case.assigned_admin_id)
            if email:
                recipients.append((case.assigned_admin_id, email))
        else:
            recipients = await _list_admin_recipients()
        for admin_id, addr in recipients:
            await _queue_email(
                addr,
                "support_case.sla_breach.admin",
                ctx,
                recipient_user_id=admin_id or None,
                recipient_user_type=UserType.ADMIN,
            )


# ---------------------------------------------------------------------------
# Precompute registrations
# ---------------------------------------------------------------------------


@register_precompute("support_cases.list", scope=PrecomputeScope.TENANT)
async def _precompute_support_cases_list(tenant_id: str) -> list:
    cases = await retrieve_support_cases(tenant_id=tenant_id, start=0, stop=100)
    return [c.model_dump(mode="json", by_alias=True) for c in cases]


@register_precompute("support_cases.admin_list", scope=PrecomputeScope.GLOBAL)
async def _precompute_support_cases_admin_list(_: str) -> list:
    cases = await retrieve_support_cases(start=0, stop=100)
    return [c.model_dump(mode="json", by_alias=True) for c in cases]


# Re-export for convenience in writer
__all__ = [
    "MAX_OPEN_CASES_PER_TENANT",
    "add_support_case",
    "add_support_case_message",
    "assign_support_case",
    "auto_close_resolved_cases",
    "alert_sla_breaches",
    "enrich_support_case_dicts",
    "nudge_awaiting_tenant_cases",
    "retrieve_cases_approaching_sla",
    "retrieve_messages_for_case",
    "retrieve_support_case_by_id",
    "retrieve_support_cases",
    "transition_support_case",
]


# Silence F401 on imports we keep only for typing parity.
_ = asyncio
