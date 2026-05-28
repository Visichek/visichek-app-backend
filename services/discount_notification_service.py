"""Queue-driven fan-out of "a discount is available" notifications + emails.

A newly-created discount should reach the tenants who can actually use it:

  * ``scope == "tenant"`` → the one targeted tenant's active super_admins.
  * ``scope == "plan"``   → every tenant currently subscribed (active /
    trialing) to one of the discount's target plans.
  * ``scope == "global"`` → every tenant with an active / trialing
    subscription.

Doing this inline in the discount-create write would turn one global
discount into a single multi-thousand-tenant task that blocks the writes
worker and risks timing out. Instead the work is split across three stages,
each bounded and spread across the default (``celery``) queue so it never
starves the write / precompute / gate workers:

  1. ``enqueue_discount_announcement`` — called by the write handler. Cheap:
     just enqueues ONE coordinator task and returns.
  2. ``discount.announce`` (coordinator) — paginates subscriptions in pages
     (cheap reads only) and enqueues many small ``discount.announce_batch``
     tasks, each carrying a bounded slice of ``(tenant_id, plan_id)`` pairs.
     It sends nothing itself.
  3. ``discount.announce_batch`` — for each tenant in its slice, resolves the
     active super_admins and sends the in-app notification + (preference-
     gated) email. Bounded runtime, safely parallel across workers.

Plain task keys (no ``db.write`` / ``gate.`` / ``precompute.`` prefix) route
to ``celery_worker.run_async_task`` on the default queue — the same place
emails and invoice-PDF generation already run.

The notification ``link`` deep-links the frontend into a checkout pre-filled
with this discount (and, for plan-scope, the plan the tenant is on), so a
click starts a checkout — see the FE contract in the create-discount
handler's response.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from core.queue.tasks import task
from schemas.imports import AccountStatus, UserType
from schemas.subscription_schema import SubscriptionStatus

logger = logging.getLogger(__name__)

# Subscriptions read per DB query while the coordinator paginates.
_PAGE_SIZE = 500
# Tenants handled by a single batch task — keeps each task's runtime bounded.
_BATCH_SIZE = 50
# Absolute ceiling on tenants notified for one discount, so a broad discount
# can never spawn an unbounded fan-out even if the subscription table grows.
_MAX_FANOUT_TENANTS = 50_000

_ACTIVE_STATUSES = [
    SubscriptionStatus.ACTIVE.value,
    SubscriptionStatus.TRIALING.value,
]


# ── helpers ─────────────────────────────────────────────────────────


def _format_value(discount_type: str, value: float) -> str:
    """Human-readable discount magnitude: ``50% off`` / ``500 off``."""
    if discount_type == "percentage":
        return f"{value:g}% off"
    return f"{value:g} off"


def _build_checkout_link(*, discount_id: str, code: str, plan_id: Optional[str]) -> str:
    """Deep link the FE uses to open a checkout pre-filled with this discount.

    The frontend reads ``discountId`` / ``discountCode`` (and the optional
    ``planId``) off the query string and POSTs them to
    ``/v1/checkout/sessions``.
    """
    link = f"/app/billing/checkout?discountId={discount_id}&discountCode={code}"
    if plan_id:
        link += f"&planId={plan_id}"
    return link


def _build_messages(
    *,
    code: str,
    name: str,
    discount_type: str,
    value: float,
    valid_until: Optional[int],
) -> tuple[str, str]:
    """Return ``(in_app_body, email_message)`` for the notification."""
    magnitude = _format_value(discount_type, value)
    body = f"Use code {code} for {magnitude} on your subscription."
    email_message = (
        f"the discount '{name}' ({code}) gives you {magnitude} on your subscription."
    )
    if valid_until:
        import datetime

        expiry = datetime.datetime.fromtimestamp(
            valid_until, tz=datetime.timezone.utc
        ).strftime("%d %b %Y")
        email_message += f" It is valid until {expiry}."
    return body, email_message


async def _active_super_admins(tenant_id: str) -> list:
    """Active super_admins for a tenant (the billing-capable role)."""
    from repositories.system_user_repo import get_system_users

    try:
        return await get_system_users(
            {
                "tenant_id": tenant_id,
                "role": "super_admin",
                "account_status": AccountStatus.ACTIVE.value,
            }
        )
    except Exception:
        logger.warning(
            "discount fan-out: super_admin lookup failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        return []


def _chunked(pairs: List[list], size: int):
    for i in range(0, len(pairs), size):
        yield pairs[i : i + size]


def _enqueue(task_key: str, payload: dict) -> bool:
    """Best-effort enqueue. Returns False (and logs) if the queue is down /
    unconfigured — callers treat fan-out as fire-and-forget."""
    from core.queue.manager import QueueManager

    try:
        QueueManager.get_instance().enqueue(task_key=task_key, payload=payload)
        return True
    except Exception:
        logger.warning(
            "discount fan-out: enqueue failed task_key=%s", task_key, exc_info=True
        )
        return False


# ── stage 1: entrypoint (called from the write handler) ─────────────


def enqueue_discount_announcement(
    *,
    discount_id: str,
    code: str,
    name: str,
    scope: str,
    discount_type: str,
    value: float,
    target_tenant_id: Optional[str] = None,
    target_plan_ids: Optional[List[str]] = None,
    valid_until: Optional[int] = None,
) -> None:
    """Enqueue the coordinator task. Cheap + sync — safe to call from a
    write handler. Never raises."""
    _enqueue(
        "discount.announce",
        {
            "discount_id": discount_id,
            "code": code,
            "name": name,
            "scope": scope,
            "discount_type": discount_type,
            "value": value,
            "target_tenant_id": target_tenant_id,
            "target_plan_ids": list(target_plan_ids or []),
            "valid_until": valid_until,
        },
    )


# ── stage 2: coordinator (paginate + enqueue batches) ───────────────


@task("discount.announce")
async def _announce_coordinator(
    discount_id: str,
    code: str,
    name: str,
    scope: str,
    discount_type: str,
    value: float,
    target_tenant_id: Optional[str] = None,
    target_plan_ids: Optional[List[str]] = None,
    valid_until: Optional[int] = None,
) -> dict:
    """Resolve recipient tenants in bounded pages and enqueue batch tasks.

    Does only cheap paginated subscription reads + enqueues; it sends no
    notifications itself, so it returns quickly regardless of fan-out size.
    """
    meta = {
        "discount_id": discount_id,
        "code": code,
        "name": name,
        "discount_type": discount_type,
        "value": value,
        "valid_until": valid_until,
    }

    if scope == "tenant":
        if not target_tenant_id:
            return {"scope": scope, "tenants": 0, "batches": 0}
        plan_id = (
            target_plan_ids[0]
            if target_plan_ids and len(target_plan_ids) == 1
            else None
        )
        batches = _emit_batches([[target_tenant_id, plan_id]], meta)
        return {"scope": scope, "tenants": 1, "batches": batches}

    # plan / global: paginate the matching active subscriptions.
    base_filter: dict
    if scope == "plan":
        if not target_plan_ids:
            return {"scope": scope, "tenants": 0, "batches": 0}
        base_filter = {
            "plan_id": {"$in": list(target_plan_ids)},
            "status": {"$in": _ACTIVE_STATUSES},
        }
        carry_plan = True  # plan-scope: pre-fill the plan the tenant is on
    elif scope == "global":
        base_filter = {"status": {"$in": _ACTIVE_STATUSES}}
        carry_plan = False  # global applies to any plan — FE picks at checkout
    else:
        return {"scope": scope, "tenants": 0, "batches": 0}

    from repositories.subscription_repo import get_subscriptions

    seen: set[str] = set()
    pending: List[list] = []
    total_tenants = 0
    total_batches = 0
    page = 0

    while total_tenants < _MAX_FANOUT_TENANTS:
        start = page * _PAGE_SIZE
        try:
            subs = await get_subscriptions(
                base_filter, start=start, stop=start + _PAGE_SIZE
            )
        except Exception:
            logger.warning(
                "discount fan-out: subscription page read failed discount=%s page=%d",
                discount_id,
                page,
                exc_info=True,
            )
            break
        if not subs:
            break

        for sub in subs:
            tid = sub.tenant_id
            if not tid or tid in seen:
                continue
            seen.add(tid)
            pending.append([tid, sub.plan_id if carry_plan else None])
            total_tenants += 1
            if len(pending) >= _BATCH_SIZE:
                total_batches += _emit_batches(pending, meta)
                pending = []
            if total_tenants >= _MAX_FANOUT_TENANTS:
                break

        if len(subs) < _PAGE_SIZE:
            break
        page += 1

    if pending:
        total_batches += _emit_batches(pending, meta)

    logger.info(
        "discount fan-out: coordinator done discount=%s scope=%s tenants=%d batches=%d",
        discount_id,
        scope,
        total_tenants,
        total_batches,
    )
    return {"scope": scope, "tenants": total_tenants, "batches": total_batches}


def _emit_batches(pairs: List[list], meta: dict) -> int:
    """Enqueue one batch task per ``_BATCH_SIZE`` chunk. Returns batches emitted."""
    count = 0
    for chunk in _chunked(pairs, _BATCH_SIZE):
        if _enqueue("discount.announce_batch", {"pairs": chunk, "meta": meta}):
            count += 1
    return count


# ── stage 3: batch handler (resolve recipients + send) ──────────────


@task("discount.announce_batch")
async def _announce_batch(pairs: List[list], meta: dict) -> dict:
    """Notify a bounded slice of tenants. Each pair is ``[tenant_id, plan_id]``.

    Per-tenant failures are logged and skipped so one bad tenant never
    fails the rest of the batch.
    """
    from services.notification_service import send_notification

    body, email_message = _build_messages(
        code=meta["code"],
        name=meta["name"],
        discount_type=meta["discount_type"],
        value=meta["value"],
        valid_until=meta.get("valid_until"),
    )
    discount_id = meta["discount_id"]
    code = meta["code"]

    delivered = 0
    for pair in pairs:
        tenant_id = pair[0] if pair else None
        plan_id = pair[1] if pair and len(pair) > 1 else None
        if not tenant_id:
            continue
        link = _build_checkout_link(discount_id=discount_id, code=code, plan_id=plan_id)
        for su in await _active_super_admins(tenant_id):
            uid = getattr(su, "id", None)
            if not uid:
                continue
            try:
                await send_notification(
                    user_id=uid,
                    user_type=UserType.SYSTEM_USER,
                    title="A discount is available",
                    body=body,
                    type="success",
                    link=link,
                    tenant_id=tenant_id,
                    resource_type="discount",
                    resource_id=discount_id,
                    email_template_key="notif_discount_available",
                    email_context={"message": email_message},
                    preference_flag="email_on_subscription_alert",
                )
                delivered += 1
            except Exception:
                logger.warning(
                    "discount fan-out: notify failed discount=%s user=%s",
                    discount_id,
                    uid,
                    exc_info=True,
                )

    return {"tenants": len(pairs), "notifications": delivered}


# Recipient-resolution map kept for callers/tests that want the resolved set
# without going through the queue (not used on the hot path).
async def resolve_recipient_tenants(
    *,
    scope: str,
    target_tenant_id: Optional[str],
    target_plan_ids: Optional[List[str]],
) -> Dict[str, Optional[str]]:
    """Map ``{tenant_id: plan_id_to_checkout_or_None}`` for the discount scope."""
    if scope == "tenant":
        plan_id = (
            target_plan_ids[0]
            if target_plan_ids and len(target_plan_ids) == 1
            else None
        )
        return {target_tenant_id: plan_id} if target_tenant_id else {}

    from repositories.subscription_repo import get_subscriptions

    if scope == "plan":
        if not target_plan_ids:
            return {}
        filt = {
            "plan_id": {"$in": list(target_plan_ids)},
            "status": {"$in": _ACTIVE_STATUSES},
        }
        carry = True
    elif scope == "global":
        filt = {"status": {"$in": _ACTIVE_STATUSES}}
        carry = False
    else:
        return {}

    out: Dict[str, Optional[str]] = {}
    subs = await get_subscriptions(filt, start=0, stop=_MAX_FANOUT_TENANTS)
    for sub in subs:
        if sub.tenant_id and sub.tenant_id not in out:
            out[sub.tenant_id] = sub.plan_id if carry else None
    return out
