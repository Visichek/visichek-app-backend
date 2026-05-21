"""Real-time notification delivery: Redis pub/sub fan-out + SSE stream.

This module is the push layer that sits on top of the existing
notification CRUD (``services/notification_service.py``). It exists to
satisfy the "in-app unread badges update in near-real-time, agree with
each other, and scale without polling" spec:

  * ``GET /v1/notifications/stream`` (in ``api/v1/notification_route.py``)
    holds a Server-Sent-Events connection per authenticated user+tenant
    and pushes the FULL, ABSOLUTE state ``{total, counts}`` on every
    change. Because every event carries absolute truth, a dropped /
    duplicated / out-of-order event is self-correcting — the next event
    overwrites it.

  * Cross-instance delivery rides Redis pub/sub keyed by user. A
    notification created on instance A is published to the channel the
    user's SSE connection on instance B is subscribed to, so delivery
    never depends on which app instance happens to hold the socket.

The two publish entry points:

  * :func:`publish_notification_created` — called from
    ``send_notification`` right after the row lands in MongoDB.
  * :func:`publish_notification_changed` — called from the queued write
    handlers in ``services/notification_writer.py`` after a
    read / read-all / delete mutation commits.

Both are best-effort: a Redis outage degrades the system to the polling
fallback (the FE keeps calling ``/summary``) but never breaks the
in-app notification itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

from schemas.imports import UserType

logger = logging.getLogger(__name__)

# Heartbeat cadence. The spec requires a comment heartbeat every 20s so
# proxies / load balancers don't drop an idle connection (the acceptance
# criteria call out "past 60s idle through the LB").
_HEARTBEAT_SECONDS = 20.0

# How often, while a stream is open, we re-validate the caller's token so
# an expired session closes the stream (spec: "On auth expiry: close the
# stream. The FE refreshes and reconnects.").
_AUTH_RECHECK_SECONDS = 30.0

# pubsub poll granularity. Small enough that disconnects + heartbeats are
# noticed promptly, large enough to avoid a busy-loop.
_POLL_TIMEOUT_SECONDS = 1.0

_EVENT_CREATED = "notification.created"
_EVENT_CHANGED = "notification.changed"

# Internal key the publisher uses to carry the event name through the
# pub/sub payload; stripped before the data reaches the SSE client.
_EVENT_KEY = "_event"


# ── Async Redis client (lazy singleton) ─────────────────────────────


_async_redis = None


def _get_async_redis():
    """Return a process-wide async Redis client, created on first use.

    Uses ``redis.asyncio`` (bundled with redis-py). ``decode_responses``
    mirrors ``core.redis_cache.cache_db`` so pub/sub messages arrive as
    ``str`` rather than ``bytes``.
    """
    global _async_redis
    if _async_redis is None:
        import redis.asyncio as aioredis

        from core.settings import get_settings

        _async_redis = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
        )
    return _async_redis


def _channel(user_type: UserType, user_id: str) -> str:
    """Per-user pub/sub channel.

    A user is uniquely identified by ``(user_type, user_id)`` — admins and
    system users live in separate collections and could in principle share
    an ObjectId, so both components are part of the key. The tenant is
    implied by the user, so it does not need to be in the channel name; the
    SSE connection is already scoped to the authenticated principal.
    """
    return f"notif:events:{user_type}:{user_id}"


# ── Publish side ─────────────────────────────────────────────────────


async def _publish(
    user_type: UserType, user_id: str, event_name: str, data: dict
) -> None:
    """Publish one event to the user's channel. Never raises."""
    payload = dict(data)
    payload[_EVENT_KEY] = event_name
    try:
        client = _get_async_redis()
        await client.publish(_channel(user_type, user_id), json.dumps(payload))
    except Exception:
        logger.warning(
            "notification_stream: publish failed user_type=%s user_id=%s event=%s",
            user_type,
            user_id,
            event_name,
            exc_info=True,
        )


async def publish_notification_created(notification) -> None:
    """Fan out a ``notification.created`` event with absolute state.

    ``notification`` is a ``NotificationOut``. The event carries the new
    notification's identity (so the FE can render a toast / dedupe by id)
    plus the recomputed absolute ``{total, counts}`` for the user.
    """
    user_id = notification.user_id or ""
    user_type = notification.user_type
    if not user_id or not user_type:
        return

    from services.notification_service import (
        _classify_link_to_bucket,
        compute_notification_state,
    )

    try:
        state = await compute_notification_state(user_id=user_id, user_type=user_type)
    except Exception:
        logger.warning(
            "notification_stream: state compute failed for created event user_id=%s",
            user_id,
            exc_info=True,
        )
        return

    notif_type = notification.type
    # ``type`` is a NotificationType enum on the model — emit its value.
    type_value = getattr(notif_type, "value", notif_type)

    data = {
        "id": notification.id,
        "type": type_value,
        "link": notification.link,
        "bucket": _classify_link_to_bucket(notification.link),
        "total": state["total"],
        "counts": state["counts"],
    }
    await _publish(user_type, user_id, _EVENT_CREATED, data)


async def publish_notification_changed(user_id: str, user_type: UserType) -> None:
    """Fan out a ``notification.changed`` event with absolute state.

    Fired for read / read-all / delete so every open session for the user
    (on any device / tab / instance) converges on the same badge counts
    without a manual refresh.
    """
    if not user_id or not user_type:
        return

    from services.notification_service import compute_notification_state

    try:
        state = await compute_notification_state(user_id=user_id, user_type=user_type)
    except Exception:
        logger.warning(
            "notification_stream: state compute failed for changed event user_id=%s",
            user_id,
            exc_info=True,
        )
        return

    await _publish(user_type, user_id, _EVENT_CHANGED, dict(state))


# ── Stream side (SSE generator) ──────────────────────────────────────


def _format_sse(event_id: int, event: str, data: dict) -> str:
    """Render a single SSE event frame.

    Each frame carries an ``id:`` so the browser sends ``Last-Event-ID``
    on reconnect, and an ``event:`` name the FE listens on. ``data`` is a
    compact JSON object. Keys are single words (``total``, ``counts``,
    ``id`` …) so they need no camel/snake conversion — which matters
    because SSE responses bypass ``CaseConversionMiddleware`` (it only
    rewrites ``application/json``).
    """
    return (
        f"id: {event_id}\n"
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


async def _token_still_valid(jwt_token: str) -> bool:
    """True while the caller's access token resolves (not expired/revoked)."""
    if not jwt_token:
        return False
    try:
        from repositories.tokens_repo import get_access_token

        return await get_access_token(accessToken=jwt_token) is not None
    except Exception:
        # Fail open on a transient DB/Redis blip — a single failed lookup
        # shouldn't tear down a live stream. A genuinely expired token will
        # keep returning None and close the stream on the next tick.
        return True


async def stream_notifications(
    request,
    *,
    user_id: str,
    user_type: UserType,
    jwt_token: str,
) -> AsyncIterator[str]:
    """Async generator backing ``GET /v1/notifications/stream``.

    Subscribes to the user's Redis pub/sub channel, emits an immediate
    absolute-state snapshot, then forwards every published event. Sends a
    heartbeat comment every ``_HEARTBEAT_SECONDS`` and tears down on client
    disconnect or token expiry.
    """
    client = _get_async_redis()
    pubsub = client.pubsub()
    channel = _channel(user_type, user_id)

    # Continue the event-id sequence from the client's Last-Event-ID when it
    # reconnects. Replay isn't needed (every event is absolute) — but keeping
    # ids monotonic across a reconnect avoids the browser re-seeing an id.
    last_event_id = 0
    try:
        raw = request.headers.get("last-event-id")
        if raw:
            last_event_id = int(raw)
    except (TypeError, ValueError):
        last_event_id = 0

    try:
        await pubsub.subscribe(channel)
    except Exception:
        logger.warning(
            "notification_stream: subscribe failed user_id=%s; closing stream",
            user_id,
            exc_info=True,
        )
        return

    try:
        # Initial snapshot so a freshly connected client is correct before
        # the first real change arrives.
        from services.notification_service import compute_notification_state

        try:
            state = await compute_notification_state(
                user_id=user_id, user_type=user_type
            )
            last_event_id += 1
            yield _format_sse(last_event_id, _EVENT_CHANGED, dict(state))
        except Exception:
            logger.warning(
                "notification_stream: initial snapshot failed user_id=%s",
                user_id,
                exc_info=True,
            )

        last_heartbeat = time.monotonic()
        last_auth_check = time.monotonic()

        while True:
            if await request.is_disconnected():
                break

            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_POLL_TIMEOUT_SECONDS,
                )
            except Exception:
                logger.warning(
                    "notification_stream: get_message failed user_id=%s",
                    user_id,
                    exc_info=True,
                )
                break

            if message is not None:
                data = message.get("data")
                if isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                        event_name = parsed.pop(_EVENT_KEY, _EVENT_CHANGED)
                        last_event_id += 1
                        yield _format_sse(last_event_id, event_name, parsed)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "notification_stream: bad pubsub payload user_id=%s",
                            user_id,
                        )

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            if now - last_auth_check >= _AUTH_RECHECK_SECONDS:
                if not await _token_still_valid(jwt_token):
                    # Session expired — close so the FE can refresh + reconnect.
                    break
                last_auth_check = now
    except asyncio.CancelledError:
        # Normal teardown when the client goes away — re-raise so the
        # server can finish cancelling.
        raise
    finally:
        try:
            await pubsub.unsubscribe(channel)
            # redis-py renamed PubSub.close() → aclose() across versions;
            # prefer aclose() and fall back so teardown works on both.
            closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if closer is not None:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            logger.debug(
                "notification_stream: pubsub teardown failed user_id=%s",
                user_id,
                exc_info=True,
            )
