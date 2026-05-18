"""Per-request subscription state watchdog.

Goal: every tenant request flows through a one-trip Redis check that
classifies the tenant's subscription as one of:

  * ``healthy``        — active and more than ``EXPIRING_SOON_SECONDS``
                         away from ``current_period_end``. Cached so the
                         next request short-circuits this whole service.
  * ``expiring_soon``  — active but within ``EXPIRING_SOON_SECONDS`` of
                         expiry. The tenant id is pushed onto the
                         watchlist sorted-set so the background drain
                         can move them onto the Free plan once the
                         window closes.
  * ``expired``        — ``current_period_end`` is in the past but the
                         row is still marked ACTIVE/TRIALING. Same
                         watchlist treatment as ``expiring_soon`` —
                         the drain runs every 60s so the lag is bounded.
  * ``missing``        — no active / trialing subscription at all. We
                         provision a Free-plan subscription synchronously
                         (per the product rule: "without a plan? Free,
                         without further consideration"). Returns
                         ``"healthy"`` after provisioning so the caller
                         doesn't have to re-evaluate.
  * ``watchlisted``    — already enqueued for the drain on a previous
                         request; we skip re-enqueueing to keep the
                         sorted set small.

The middleware calls ``evaluate_tenant_subscription`` BEFORE resolving
the plan, so the lazy fallback that lived in the middleware moves into
this module's ``missing`` branch.

The APScheduler-driven ``drain_subscription_watchlist`` job pops up to
``DRAIN_BATCH_SIZE`` entries off the sorted set every minute and routes
each tenant through ``transition_tenant_to_free_plan``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional, cast

from core.redis_cache import cache_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables — match the product rule from the original spec.
# ---------------------------------------------------------------------------

#: A subscription is considered "expiring soon" once the remaining lifetime
#: drops below this many seconds. Default: 2 hours 30 minutes.
EXPIRING_SOON_SECONDS = 2 * 3600 + 30 * 60

#: Upper bound on how long a "healthy" classification stays cached. Even
#: when the subscription is years from expiry we still re-check at least
#: once per day so that admin-side cancellations propagate quickly.
HEALTHY_MAX_TTL_SECONDS = 24 * 3600

#: Number of watchlist entries the background drain pops per tick. Keeps
#: the worker's per-iteration runtime bounded so a backlog can't starve
#: the rest of the APScheduler queue.
DRAIN_BATCH_SIZE = 100

#: Redis keys.
STATE_KEY_PREFIX = "subscription_state:"
WATCHLIST_KEY = "subscription_watchlist"


# ---------------------------------------------------------------------------
# Cache helpers — wrapped so a Redis outage degrades to "always re-evaluate"
# instead of breaking every request.
# ---------------------------------------------------------------------------


def _state_key(tenant_id: str) -> str:
    return f"{STATE_KEY_PREFIX}{tenant_id}"


def _cached_state(tenant_id: str) -> Optional[str]:
    try:
        return cast(Optional[str], cache_db.get(_state_key(tenant_id)))
    except Exception:
        logger.warning(
            "subscription_watchdog: cache_db.get failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        return None


def _cache_healthy(tenant_id: str, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        cache_db.setex(_state_key(tenant_id), ttl_seconds, "healthy")
    except Exception:
        logger.warning(
            "subscription_watchdog: cache_db.setex failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _clear_cached_state(tenant_id: str) -> None:
    try:
        cache_db.delete(_state_key(tenant_id))
    except Exception:
        pass


def _enqueue_watchlist(tenant_id: str, *, now_ts: int) -> bool:
    """Add ``tenant_id`` to the watchlist. Returns True if it was newly added.

    The sorted-set score is the enqueue timestamp so the drain can pop
    oldest-first via ``ZRANGE`` + ``ZREM``.
    """
    try:
        # ZADD with NX so we only update the score on the first insert —
        # otherwise frequent requests would keep bumping the score and
        # the drain would always look at the same tenants.
        added = cast(
            Any,
            cache_db.zadd(WATCHLIST_KEY, {tenant_id: now_ts}, nx=True),
        )
        return int(added or 0) > 0
    except Exception:
        logger.warning(
            "subscription_watchdog: zadd watchlist failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        return False


def _already_watchlisted(tenant_id: str) -> bool:
    try:
        score = cast(Any, cache_db.zscore(WATCHLIST_KEY, tenant_id))
        return score is not None
    except Exception:
        return False


def _pop_watchlist_batch(batch_size: int) -> list[str]:
    """Pop the oldest ``batch_size`` tenant ids off the watchlist.

    Uses a pipelined ``ZRANGE`` + ``ZREM`` so the read + delete is
    atomic-ish (no other worker will see the same id in a concurrent
    drain iteration once the ZREM commits).
    """
    try:
        pipe = cast(Any, cache_db.pipeline())
        pipe.zrange(WATCHLIST_KEY, 0, max(0, batch_size - 1))
        pipe.zremrangebyrank(WATCHLIST_KEY, 0, max(0, batch_size - 1))
        results = pipe.execute()
    except Exception:
        logger.warning(
            "subscription_watchdog: watchlist drain pipeline failed", exc_info=True
        )
        return []
    members = results[0] if results else []
    return [str(m) for m in members if m]


# ---------------------------------------------------------------------------
# Classification — public surface called by the middleware.
# ---------------------------------------------------------------------------


async def evaluate_tenant_subscription(tenant_id: str) -> str:
    """Classify the tenant's subscription state and apply side-effects.

    Side-effects (per branch):
      * healthy  → write ``healthy`` to the state cache with a TTL up to
                   ``HEALTHY_MAX_TTL_SECONDS``.
      * missing  → call ``ensure_tenant_default_subscription`` and treat
                   the tenant as healthy immediately afterwards. The
                   "without a plan? Free, immediately" product rule.
      * expiring_soon / expired → push onto the watchlist sorted set so
                   the drain job can move the tenant to Free.

    Returns the classification string. Callers can ignore the return
    value; the function exists for tests + logging instrumentation.
    """
    if not tenant_id:
        return "unknown"

    cached = _cached_state(tenant_id)
    if cached == "healthy":
        return "healthy"

    # Lazy import to avoid a service-import cycle at app boot.
    from repositories.subscription_repo import get_subscription
    from schemas.subscription_schema import SubscriptionStatus

    sub = None
    try:
        sub = await get_subscription(
            {
                "tenant_id": tenant_id,
                "status": {
                    "$in": [
                        SubscriptionStatus.ACTIVE.value,
                        SubscriptionStatus.TRIALING.value,
                    ]
                },
            }
        )
    except Exception:
        logger.warning(
            "subscription_watchdog: get_subscription failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        return "unknown"

    if sub is None:
        # No active subscription — provision Free immediately. Product
        # rule: "WITHOUT FURTHER CONSIDERATION".
        try:
            from services.plan_bootstrap import ensure_tenant_default_subscription

            await ensure_tenant_default_subscription(tenant_id=tenant_id)
        except Exception:
            logger.warning(
                "subscription_watchdog: ensure_tenant_default_subscription failed tenant=%s",
                tenant_id,
                exc_info=True,
            )
            return "missing"
        # Provisioning succeeded — Free plan period_end is ~100 years
        # out, so the tenant is healthy now. Cache with the max TTL.
        _cache_healthy(tenant_id, HEALTHY_MAX_TTL_SECONDS)
        return "missing"

    now = int(time.time())
    period_end = int(getattr(sub, "current_period_end", 0) or 0)
    seconds_left = period_end - now

    if period_end == 0 or seconds_left <= 0:
        # Past expiry but row still flagged ACTIVE/TRIALING. Queue for
        # the drain — the renewal scheduler should have caught this
        # but the watchlist is the second-chance safety net.
        if not _already_watchlisted(tenant_id):
            _enqueue_watchlist(tenant_id, now_ts=now)
        return "expired"

    if seconds_left <= EXPIRING_SOON_SECONDS:
        if not _already_watchlisted(tenant_id):
            _enqueue_watchlist(tenant_id, now_ts=now)
        return "expiring_soon"

    # Healthy. TTL = min(max, until_expiring_soon) so we re-check
    # exactly when the sub crosses into the "expiring soon" window.
    until_expiring = max(60, seconds_left - EXPIRING_SOON_SECONDS)
    ttl = min(HEALTHY_MAX_TTL_SECONDS, until_expiring)
    _cache_healthy(tenant_id, ttl)
    return "healthy"


# ---------------------------------------------------------------------------
# Background drain — registered as an APScheduler job in main.py.
# ---------------------------------------------------------------------------


async def drain_subscription_watchlist(
    batch_size: int = DRAIN_BATCH_SIZE,
) -> dict[str, Any]:
    """Drain up to ``batch_size`` tenants off the watchlist.

    For each tenant: route them through ``transition_tenant_to_free_plan``
    so any non-Free subscription past its grace window is replaced with
    a fresh Free-plan row. Failures per-tenant are logged and the drain
    continues — a single broken tenant never blocks the rest.

    Returns a small summary dict for logging.
    """
    members = _pop_watchlist_batch(batch_size)
    if not members:
        return {"drained": 0, "succeeded": 0, "failed": 0}

    from services.subscription_service import transition_tenant_to_free_plan

    succeeded = 0
    failed = 0

    async def _transition(tid: str) -> bool:
        try:
            await transition_tenant_to_free_plan(
                tenant_id=tid,
                reason="watchdog_drain_expired_or_expiring",
                actor_id="system:subscription_watchdog",
                actor_role="admin",
            )
            _clear_cached_state(tid)
            return True
        except Exception:
            logger.warning(
                "subscription_watchdog: transition_to_free failed tenant=%s",
                tid,
                exc_info=True,
            )
            return False

    results = await asyncio.gather(
        *(_transition(tid) for tid in members), return_exceptions=False
    )
    for ok in results:
        if ok:
            succeeded += 1
        else:
            failed += 1

    logger.info(
        "subscription_watchdog: drained=%d succeeded=%d failed=%d",
        len(members),
        succeeded,
        failed,
    )
    return {"drained": len(members), "succeeded": succeeded, "failed": failed}
