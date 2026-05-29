"""Push notification channel: subscription management + the send task.

This is the fourth notification channel, sitting alongside in-app
(MongoDB row), real-time SSE (``notification_stream_service``), and email
(``_dispatch_email_for_notification``). The single fan-out point —
``services/notification_service.send_notification`` — enqueues a
``push.send`` task here, so every ``notify_*`` helper gains OS/browser
push delivery for free.

Transport is provider-agnostic: this module only talks to
``core.push.PushManager``, which wraps the configured provider (Web Push
today, FCM/APNs/OneSignal tomorrow). Switching providers is a config
change — see ``PUSH_PROVIDER`` and ``core/push/``.

Why a queued task: a provider send is a blocking HTTPS POST per device.
Doing that inline on the request path (or in ``send_notification``) would
block the event loop, so the actual send runs on the default celery queue,
fanned out per notification — exactly like the email path's
``dispatch="auto"``. A subscription the provider reports as Gone (e.g. Web
Push 404/410) is pruned so dead endpoints don't accumulate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.queue.tasks import task
from core.push.types import PushMessage, PushSubscriptionInfo
from schemas.imports import UserType
from schemas.push_subscription_schema import PushSubscriptionOut

logger = logging.getLogger(__name__)


# ── Subscription management (called synchronously from the route) ────


async def register_push_subscription(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_id: str,
    user_type: UserType,
    tenant_id: Optional[str],
    expiration_time: Optional[int],
    user_agent: Optional[str],
) -> PushSubscriptionOut:
    """Persist (or refresh) a browser push subscription for a user."""
    import time

    from repositories.push_subscription_repo import upsert_push_subscription

    return await upsert_push_subscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_id=user_id,
        user_type=user_type,
        tenant_id=tenant_id,
        expiration_time=expiration_time,
        user_agent=user_agent,
        now=int(time.time()),
    )


async def unregister_push_subscription(
    *, endpoint: str, user_id: str, user_type: UserType
) -> int:
    """Remove a subscription the user explicitly unsubscribed from."""
    from repositories.push_subscription_repo import (
        delete_push_subscription_for_user,
    )

    return await delete_push_subscription_for_user(
        endpoint=endpoint, user_id=user_id, user_type=user_type
    )


def get_push_client_config() -> dict:
    """Provider-agnostic config the frontend needs to register a device."""
    from core.push.manager import PushManager

    return PushManager.get_instance().client_config().to_response()


# ── Enqueue (called from notification_service.send_notification) ─────


def enqueue_push(
    *,
    user_id: str,
    user_type: str,
    title: str,
    body: str,
    link: Optional[str] = None,
    notification_id: Optional[str] = None,
    type: str = "info",
) -> None:
    """Best-effort: enqueue a ``push.send`` for the user. Never raises.

    ``user_type`` is the plain string value (``"admin"`` / ``"system_user"``
    / ``"user"``) so the payload stays JSON-serialisable for celery.
    """
    from core.queue.manager import QueueManager

    try:
        QueueManager.get_instance().enqueue(
            task_key="push.send",
            payload={
                "user_id": user_id,
                "user_type": user_type,
                "title": title,
                "body": body,
                "link": link,
                "notification_id": notification_id,
                "type": type,
            },
        )
    except Exception:
        # Queue unconfigured / down — push is fire-and-forget, the in-app
        # notification has already landed, so just log and move on.
        logger.warning(
            "push: enqueue failed user_id=%s notification_id=%s",
            user_id,
            notification_id,
            exc_info=True,
        )


# ── The send task (runs on the default celery queue) ────────────────


@task("push.send")
async def _push_send(
    user_id: str,
    user_type: str,
    title: str,
    body: str,
    link: Optional[str] = None,
    notification_id: Optional[str] = None,
    type: str = "info",
) -> dict:
    """Deliver one notification to all of a user's devices via the provider.

    Looks up every push subscription the user has registered, sends the
    message to each through ``PushManager``, and prunes any subscription the
    provider reports as Gone. Returns a ``{sent, pruned, total}`` summary
    (logged in ``queue_job_log.result``).
    """
    from core.push.manager import PushManager
    from repositories.push_subscription_repo import (
        delete_push_subscription_by_endpoint,
        get_push_subscriptions_for_user,
    )

    try:
        ut = UserType(user_type)
    except ValueError:
        logger.warning("push.send: unknown user_type=%s", user_type)
        return {"sent": 0, "pruned": 0, "total": 0}

    subscriptions = await get_push_subscriptions_for_user(user_id, ut)
    if not subscriptions:
        return {"sent": 0, "pruned": 0, "total": 0}

    manager = PushManager.get_instance()
    message = PushMessage(
        title=title,
        body=body,
        url=link or "/",
        notification_id=notification_id,
        type=type,
    )

    sent = 0
    pruned = 0
    for sub in subscriptions:
        subscription = PushSubscriptionInfo(
            endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth
        )
        # Provider send is blocking (HTTPS POST) — keep it off the loop.
        result = await asyncio.to_thread(
            manager.send, subscription=subscription, message=message
        )
        if result.ok:
            sent += 1
        elif result.gone:
            try:
                await delete_push_subscription_by_endpoint(sub.endpoint)
                pruned += 1
            except Exception:
                logger.warning(
                    "push.send: prune failed endpoint=%s", sub.endpoint, exc_info=True
                )

    logger.info(
        "push.send: user_id=%s sent=%d pruned=%d total=%d",
        user_id,
        sent,
        pruned,
        len(subscriptions),
    )
    return {"sent": sent, "pruned": pruned, "total": len(subscriptions)}
