"""Public check-in status: one-shot compute + bounded Redis long-poll (WS5).

The kiosk waiting screen ("Waiting for the front desk to clear you…") polls
``GET /v1/public/checkins/{checkin_id}/status``. With ``wait=1`` the request
parks on this module's bounded pub/sub wait (≤ ``MAX_WAIT_SECONDS``) so a
receptionist decision reaches the kiosk within ~a second instead of a polling
interval.

Trigger model mirrors ``services/dashboard_stream_service.py``: write paths
publish a bare ``"1"`` nudge on ``checkin:{checkin_id}`` and the parked
request recomputes the ABSOLUTE state itself — a dropped / duplicated /
out-of-order nudge is self-correcting because the next poll converges.

Everything Redis-side is best-effort: a Redis outage degrades ``wait=1`` to
an instant-return poll and a publish failure never breaks an approval.

Scope note (plan Task B2 item 5): this serves the kiosk ``checkins``
collection ONLY. Capability tokens are minted exclusively by the kiosk
check-in submit paths, so a ``visit_sessions`` id can never present a valid
token here — the reception-assisted (visit-session) flow already receives
its badge synchronously in the confirm / finalize response and has no
waiting screen to feed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.errors import AppException, ErrorCode
from repositories.badge_repo import get_badges_by_checkin_ids
from repositories.checkin_repo import get_checkin
from schemas.checkin_schema import CheckinState
from schemas.public_registration_schema import PublicCheckinStatusOut

logger = logging.getLogger(__name__)

# Wait-loop cadence lifted from the dashboard SSE pattern
# (dashboard_stream_service.stream_dashboard_live): pubsub.get_message with a
# 1s timeout, checked against a monotonic deadline.
_POLL_TIMEOUT_SECONDS = 1.0
# Hard cap on how long a ``wait=1`` request is held before returning the
# current state anyway (the frontend re-issues immediately on timeout).
MAX_WAIT_SECONDS = 25.0

# States the kiosk stops polling on. APPROVED is terminal even when the
# badge is None — Free-plan organizations approve without a badge artifact.
TERMINAL_CHECKIN_STATES = frozenset(
    {
        CheckinState.APPROVED.value,
        CheckinState.REJECTED.value,
        CheckinState.CHECKED_OUT.value,
    }
)


def checkin_status_channel(checkin_id: str) -> str:
    return f"checkin:{checkin_id}"


# ── Async Redis client (lazy singleton, mirrors dashboard_stream_service) ──


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


async def publish_checkin_status_nudge(checkin_id: str) -> None:
    """Nudge any kiosk long-poll parked on this check-in. Never raises.

    The payload is a bare nudge — the parked request recomputes the absolute
    status itself, so this stays a single cheap Redis publish with zero
    database cost on the approve / reject write path.
    """
    if not checkin_id:
        return
    try:
        client = _get_async_redis()
        await client.publish(checkin_status_channel(checkin_id), "1")
    except Exception:
        logger.debug(
            "checkin_status: publish nudge failed checkin=%s",
            checkin_id,
            exc_info=True,
        )


# ── Status compute (absolute state, recomputed per poll) ─────────────


def _checkin_state_value(checkin: Any) -> str:
    state = getattr(checkin, "state", None)
    return state.value if hasattr(state, "value") else str(state or "")


async def get_public_checkin_status(checkin_id: str) -> PublicCheckinStatusOut:
    """Absolute status snapshot for the public kiosk waiting screen.

    Badge fields attach only once the check-in is APPROVED and a badge
    artifact exists (Free orgs: approved + badge null is the normal
    outcome, not an error). The full ``badge`` pass reuses
    ``get_public_badge_pass`` so the kiosk badge view and the public
    ``/badge/{token}`` page render from the identical data contract.
    """
    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        # no-store even on the 404 so no cache layer ever pins a miss for a
        # check-in id that is about to exist / be retried.
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Checkin not found",
            details={"resource": "Checkin", "resource_id": checkin_id},
            headers={"Cache-Control": "no-store"},
        )

    state = _checkin_state_value(checkin)
    out = PublicCheckinStatusOut(checkin_id=checkin_id, state=state)

    if state == CheckinState.REJECTED.value:
        out.rejection_reason = getattr(checkin, "rejection_reason", None)
        return out

    if state != CheckinState.APPROVED.value:
        return out

    # Approved: attach the badge artifacts when they exist. The repo helper
    # already filters revoked badges and sorts newest-first.
    badges = await get_badges_by_checkin_ids(checkin.tenant_id, [checkin_id])
    if not badges:
        return out
    badge = badges[0]
    out.badge_token = badge.qr_code_value
    # None ⇒ MANUAL badge-expiry policy: valid until checkout / revocation.
    out.badge_expires_at = badge.expires_at
    try:
        # Lazy import — public_registration_service lazily imports back into
        # the check-in service family; keep this edge function-local.
        from services.public_registration_service import get_public_badge_pass

        out.badge = await get_public_badge_pass(badge.qr_code_value)
    except Exception:
        # The badge pass is display data — the kiosk can still deep-link
        # ``/badge/{badge_token}``. Never fail the status poll over it.
        logger.warning(
            "checkin_status: badge pass build failed checkin=%s",
            checkin_id,
            exc_info=True,
        )
    return out


# ── Wait side (bounded long-poll primitive) ──────────────────────────


async def wait_for_checkin_status_change(
    request: Any,
    checkin_id: str,
    *,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
) -> None:
    """Park until a status nudge lands for ``checkin_id``, the client
    disconnects, or the deadline passes — whichever comes first.

    Bounded long-poll primitive lifted from the dashboard SSE loop
    (``dashboard_stream_service.stream_dashboard_live``): subscribe, then
    ``pubsub.get_message(timeout=1.0)`` against a monotonic deadline.
    Always returns ``None`` — the caller recomputes the absolute state
    afterwards, so a missed or duplicated nudge is self-correcting.
    Best-effort: any Redis failure returns immediately (the endpoint
    degrades to a plain instant poll).
    """
    channel = checkin_status_channel(checkin_id)
    pubsub = None
    try:
        try:
            client = _get_async_redis()
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
        except Exception:
            logger.debug(
                "checkin_status: subscribe failed channel=%s — instant return",
                channel,
                exc_info=True,
            )
            return

        # Close the check-then-subscribe race: a decision published between
        # the caller's initial read and our subscribe would otherwise hold
        # the request for the full window. Already terminal? Return now.
        try:
            checkin = await get_checkin({"_id": checkin_id})
        except Exception:
            checkin = None
        if (
            checkin is not None
            and _checkin_state_value(checkin) in TERMINAL_CHECKIN_STATES
        ):
            return

        deadline = time.monotonic() + max_wait_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if request is not None and await request.is_disconnected():
                return
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    # Never sleep past the deadline — the hold is hard-capped.
                    timeout=min(_POLL_TIMEOUT_SECONDS, remaining),
                )
            except Exception:
                logger.debug(
                    "checkin_status: get_message failed channel=%s",
                    channel,
                    exc_info=True,
                )
                return
            if message is not None:
                return
    except asyncio.CancelledError:
        raise
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
                # redis-py version-tolerant close (aclose ≥5.0, close before).
                closer = getattr(pubsub, "aclose", None) or getattr(
                    pubsub, "close", None
                )
                if closer is not None:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:
                logger.debug(
                    "checkin_status: pubsub teardown failed channel=%s",
                    channel,
                    exc_info=True,
                )
