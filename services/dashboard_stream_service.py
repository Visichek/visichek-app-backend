"""Real-time dashboard "live KPI" delivery: Redis pub/sub nudge + SSE stream.

This is the push layer for the volatile counters on the admin platform
dashboard (``/v1/admins/dashboard/stats``) and the tenant Insights page
(``/v1/dashboard/insights``). It deliberately mirrors the notification stream
(``services/notification_stream_service.py``):

  * An SSE endpoint holds one long-lived ``text/event-stream`` connection and
    pushes the FULL, ABSOLUTE live slice ``{counters, lastUpdated}`` on every
    update. Because every event is absolute, a dropped / duplicated /
    out-of-order event is self-correcting — the next event overwrites it.

  * HYBRID trigger model:
      - Event-driven: write paths call :func:`publish_dashboard_refresh`
        (a tiny Redis nudge — NO DB work) when a live counter could have
        changed (a visitor checks in/out, an incident is filed). The open
        stream coalesces nudges and recomputes the absolute slice.
      - Periodic safety net: even with zero events, the stream recomputes and
        re-pushes every ``_PERIODIC_REFRESH_SECONDS`` so a client that missed a
        nudge always converges.

  * Only the CHEAP, volatile counters are streamed (single ``count_documents``
    each). The heavy charts / distributions / top-N rollups stay on the
    one-shot GET — streaming those on every change would be far too expensive.

Everything is best-effort: a Redis outage degrades to the polling fallback
(the FE keeps calling the one-shot GET) but never breaks a write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from core.database import db
from services.dashboard_service import _start_of_today_ts

logger = logging.getLogger(__name__)

# Cadences (seconds). Heartbeat keeps idle connections alive through proxies;
# auth recheck closes the stream on session expiry; the min-push interval
# debounces nudge storms; the periodic refresh is the safety-net converge.
_HEARTBEAT_SECONDS = 20.0
_AUTH_RECHECK_SECONDS = 30.0
_POLL_TIMEOUT_SECONDS = 1.0
_MIN_PUSH_INTERVAL_SECONDS = 3.0
_PERIODIC_REFRESH_SECONDS = 15.0

_EVENT_LIVE = "dashboard.live"
_ADMIN_CHANNEL = "dash:events:admin"


def _tenant_channel(tenant_id: str) -> str:
    return f"dash:events:tenant:{tenant_id}"


# ── Async Redis client (lazy singleton) ─────────────────────────────


_async_redis = None


def _get_async_redis():
    global _async_redis
    if _async_redis is None:
        import redis.asyncio as aioredis

        from core.settings import get_settings

        _async_redis = aioredis.from_url(
            get_settings().redis_url, decode_responses=True
        )
    return _async_redis


# ── Publish side (event-driven nudge — no DB work) ───────────────────


async def publish_dashboard_refresh(tenant_id: Optional[str] = None) -> None:
    """Nudge open dashboard streams to recompute. Best-effort, never raises.

    Always nudges the shared admin channel (platform live counters move when
    any tenant does something). When ``tenant_id`` is given, also nudges that
    tenant's channel so its Insights live slice updates. The payload is a bare
    nudge — the stream recomputes the absolute slice itself, so this stays a
    single cheap Redis publish with zero database cost on the write path.
    """
    try:
        client = _get_async_redis()
        await client.publish(_ADMIN_CHANNEL, "1")
        if tenant_id:
            await client.publish(_tenant_channel(tenant_id), "1")
    except Exception:
        logger.debug(
            "dashboard_stream: publish nudge failed tenant=%s", tenant_id, exc_info=True
        )


# ── Live-slice compute (cheap counters only) ─────────────────────────


async def compute_admin_live_slice() -> Dict[str, Any]:
    """Platform-wide volatile counters for the application-admin dashboard."""
    now = int(time.time())
    start_today = _start_of_today_ts(now)

    (
        open_incidents,
        critical_incidents,
        incidents_today,
        checkins_sessions_today,
        checkins_public_today,
        new_tenants_today,
        support_open,
        dsr_open,
    ) = await asyncio.gather(
        db["incident_logs"].count_documents(
            {"status": {"$nin": ["closed", "reported_to_ndpc", "resolved"]}}
        ),
        db["incident_logs"].count_documents({"risk_level": "critical"}),
        db["incident_logs"].count_documents({"date_created": {"$gte": start_today}}),
        db["visit_sessions"].count_documents({"check_in_time": {"$gte": start_today}}),
        db["checkins"].count_documents({"date_created": {"$gte": start_today}}),
        db["tenant_companies"].count_documents({"date_created": {"$gte": start_today}}),
        db["support_cases"].count_documents(
            {"status": {"$nin": ["closed", "resolved"]}}
        ),
        db["data_subject_requests"].count_documents(
            {"status": {"$nin": ["completed", "rejected"]}}
        ),
    )
    return {
        "counters": {
            "openIncidents": int(open_incidents),
            "criticalIncidents": int(critical_incidents),
            "incidentsToday": int(incidents_today),
            "visitorCheckInsToday": int(checkins_sessions_today)
            + int(checkins_public_today),
            "newTenantsToday": int(new_tenants_today),
            "supportCasesOpen": int(support_open),
            "dsrOpen": int(dsr_open),
        },
        "lastUpdated": now,
    }


async def compute_tenant_live_slice(tenant_id: str) -> Dict[str, Any]:
    """Tenant-wide volatile counters for the Insights live header.

    Tenant-wide (not role-scoped) on purpose: the role decides which of these
    the UI surfaces, but the counters themselves are cheap and shared."""
    now = int(time.time())
    start_today = _start_of_today_ts(now)
    base = {"tenant_id": tenant_id}

    (
        checked_in,
        approved_checkins,
        pending_approval,
        sessions_today,
        public_today,
        sessions_out_today,
        public_out_today,
        open_incidents,
        approaching,
        open_dsr,
    ) = await asyncio.gather(
        db["visit_sessions"].count_documents({**base, "status": "checked_in"}),
        db["checkins"].count_documents({**base, "state": "approved"}),
        db["checkins"].count_documents({**base, "state": "pending_approval"}),
        db["visit_sessions"].count_documents(
            {**base, "check_in_time": {"$gte": start_today}}
        ),
        db["checkins"].count_documents({**base, "date_created": {"$gte": start_today}}),
        db["visit_sessions"].count_documents(
            {**base, "check_out_time": {"$gte": start_today}}
        ),
        db["checkins"].count_documents(
            {**base, "checked_out_at": {"$gte": start_today}}
        ),
        db["incident_logs"].count_documents(
            {**base, "status": {"$nin": ["closed", "reported_to_ndpc"]}}
        ),
        db["incident_logs"].count_documents(
            {
                **base,
                "ndpc_notified": {"$ne": True},
                "notification_deadline": {"$gte": now, "$lte": now + 86400},
                "status": {"$nin": ["closed", "reported_to_ndpc"]},
            }
        ),
        db["data_subject_requests"].count_documents(
            {**base, "status": {"$nin": ["completed", "rejected"]}}
        ),
    )
    currently_active = int(checked_in) + int(approved_checkins)
    return {
        "counters": {
            "currentlyActive": currently_active,
            "awaitingCheckout": currently_active,
            "pendingApproval": int(pending_approval),
            "checkInsToday": int(sessions_today) + int(public_today),
            "checkOutsToday": int(sessions_out_today) + int(public_out_today),
            "openIncidents": int(open_incidents),
            "incidentsApproachingDeadline": int(approaching),
            "openDsr": int(open_dsr),
        },
        "lastUpdated": now,
    }


# ── Stream side (SSE generator) ──────────────────────────────────────


def _format_sse(event_id: int, event: str, data: dict) -> str:
    """Render one SSE frame. Keys are already camelCase — SSE bypasses the
    case-conversion middleware (it only rewrites application/json)."""
    return (
        f"id: {event_id}\n"
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


async def _token_still_valid(jwt_token: str) -> bool:
    if not jwt_token:
        return False
    try:
        from repositories.tokens_repo import get_access_token

        return await get_access_token(accessToken=jwt_token) is not None
    except Exception:
        # Fail open on a transient blip — a genuinely expired token keeps
        # returning None and closes the stream on the next tick.
        return True


async def stream_dashboard_live(
    request,
    *,
    channel: str,
    compute: Callable[[], Awaitable[Dict[str, Any]]],
    jwt_token: str,
) -> AsyncIterator[str]:
    """Async generator backing the dashboard live SSE endpoints.

    Subscribes to ``channel``, emits an immediate absolute snapshot, then
    recomputes + re-pushes when a nudge arrives (debounced) or on the periodic
    safety-net interval. Heartbeats every ``_HEARTBEAT_SECONDS`` and tears down
    on client disconnect or token expiry."""
    client = _get_async_redis()
    pubsub = client.pubsub()

    try:
        await pubsub.subscribe(channel)
    except Exception:
        logger.warning(
            "dashboard_stream: subscribe failed channel=%s; closing",
            channel,
            exc_info=True,
        )
        return

    event_id = 0

    async def _push() -> Optional[str]:
        nonlocal event_id
        try:
            slice_ = await compute()
        except Exception:
            logger.warning(
                "dashboard_stream: compute failed channel=%s", channel, exc_info=True
            )
            return None
        event_id += 1
        return _format_sse(event_id, _EVENT_LIVE, slice_)

    try:
        frame = await _push()  # initial snapshot
        if frame:
            yield frame

        last_push = time.monotonic()
        last_heartbeat = time.monotonic()
        last_auth_check = time.monotonic()
        dirty = False

        while True:
            if await request.is_disconnected():
                break

            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SECONDS
                )
            except Exception:
                logger.warning(
                    "dashboard_stream: get_message failed channel=%s",
                    channel,
                    exc_info=True,
                )
                break
            if message is not None:
                dirty = True

            now = time.monotonic()
            should_push = (dirty and now - last_push >= _MIN_PUSH_INTERVAL_SECONDS) or (
                now - last_push >= _PERIODIC_REFRESH_SECONDS
            )
            if should_push:
                frame = await _push()
                if frame:
                    yield frame
                last_push = now
                last_heartbeat = now
                dirty = False

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            if now - last_auth_check >= _AUTH_RECHECK_SECONDS:
                if not await _token_still_valid(jwt_token):
                    break
                last_auth_check = now
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(channel)
            closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if closer is not None:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            logger.debug(
                "dashboard_stream: pubsub teardown failed channel=%s",
                channel,
                exc_info=True,
            )


# ── Unified, access-rights-aware stream builder ──────────────────────


async def _tenant_plan_tier(tenant_id: str) -> str:
    """Best-effort plan tier for the tenant ("free" on any failure)."""
    try:
        from services.insights_service import _resolve_tier

        return await _resolve_tier(tenant_id)
    except Exception:
        return "free"


def unified_stream(request, *, principal) -> AsyncIterator[str]:
    """Single role-agnostic live stream — what gets returned depends on the
    caller's access rights (exactly like ``GET /v1/notifications/stream``):

      * application admin (``role == "admin"``) -> platform-wide live slice on
        the shared admin channel.
      * any tenant role -> that tenant's live slice on the tenant channel, with
        a ``meta`` block carrying role + plan context (planTier / isFreeFallback)
        so the FE can gate the live header the same way it gates the page.

    The caller's role is validated in the route before this is invoked, so we
    only branch on admin-vs-tenant here.
    """
    role = principal.role

    if role == "admin":

        async def _compute_admin() -> Dict[str, Any]:
            slice_ = await compute_admin_live_slice()
            slice_["meta"] = {"scope": "admin", "role": role}
            return slice_

        return stream_dashboard_live(
            request,
            channel=_ADMIN_CHANNEL,
            compute=_compute_admin,
            jwt_token=principal.jwt_token,
        )

    tenant_id = principal.tenant_id or ""

    async def _compute_tenant() -> Dict[str, Any]:
        slice_ = await compute_tenant_live_slice(tenant_id)
        tier = await _tenant_plan_tier(tenant_id)
        slice_["meta"] = {
            "scope": "tenant",
            "role": role,
            "tenantId": tenant_id,
            "planTier": tier,
            "isFreeFallback": tier == "free",
        }
        return slice_

    return stream_dashboard_live(
        request,
        channel=_tenant_channel(tenant_id),
        compute=_compute_tenant,
        jwt_token=principal.jwt_token,
    )
