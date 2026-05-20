"""In-process LRU+TTL cache for resolved access tokens.

Every authenticated request hits ``get_access_token``, which does a Mongo
round trip. On a remote Mongo that's the dominant per-request cost. This
module memoises the resolved ``accessTokenOut`` for a short window so the
hot path becomes a dict lookup.

Design decisions:

- **In-process, not Redis.** The whole point is to avoid a network hop; a
  Redis fetch would only be ~50% faster than Mongo over the same LAN.
- **Short TTL (random 2–5s per entry).** Revocation visibility is bounded
  by the TTL plus whatever propagation lag a multi-worker deployment has.
  We also invalidate explicitly on ``delete_access_token`` so a same-worker
  logout is instant. The TTL is randomised per entry so cache expirations
  fan out instead of pinning a thundering herd of Mongo reads to the same
  second — important under load when many tokens were cached during the
  same burst (e.g. a deploy that warmed caches from a load test).
- **Thread-safe.** BaseHTTPMiddleware and Starlette both dispatch across
  worker threads in some configurations, so we guard the OrderedDict with
  a lock. The operations are O(1), contention is negligible.
- **Bounded.** We cap at ``_MAX_ENTRIES`` entries (default 2000) and LRU-evict
  under pressure so a misconfigured caller can't OOM the process.

This cache stores objects keyed by the *raw JWT string* that callers pass
in, so differently-shaped inputs (JWT vs. raw ObjectId) produce distinct
entries. That's intentional — we don't want to pay the JWT decode cost on
every hit.
"""

from __future__ import annotations

import logging
import random
import time
from collections import OrderedDict
from threading import Lock
from typing import Optional

from schemas.tokens_schema import accessTokenOut

logger = logging.getLogger(__name__)

# Per-entry TTL is picked uniformly from this inclusive range so cross-worker
# revocation lag stays small (<= 5s) and cache misses are spread out instead
# of stampeding the database at a single second boundary.
MIN_TTL_SECONDS = 2
MAX_TTL_SECONDS = 5
_MAX_ENTRIES = 2000


def _pick_ttl() -> int:
    return random.randint(MIN_TTL_SECONDS, MAX_TTL_SECONDS)


# key -> (expires_at_epoch, token_out)
_cache: "OrderedDict[str, tuple[float, accessTokenOut]]" = OrderedDict()
_lock = Lock()

# Map of token_id (str(ObjectId)) -> set of cache keys pointing to it.
# Lets ``invalidate_by_token_id`` evict every JWT form of the same token,
# which matters because the same underlying token can be looked up by raw
# ObjectId string or by the JWT that wraps it.
_by_token_id: dict[str, set[str]] = {}


def get(key: str) -> Optional[accessTokenOut]:
    """Return a cached token if present and unexpired; refreshes LRU order."""
    if not key:
        return None
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        expires, token = hit
        if expires < now:
            _drop_locked(key)
            return None
        _cache.move_to_end(key)
        return token


def put(key: str, token: accessTokenOut, ttl: Optional[int] = None) -> None:
    """Cache ``token`` under ``key``.

    ``ttl`` defaults to a uniformly-random value in ``[MIN_TTL_SECONDS,
    MAX_TTL_SECONDS]`` so cross-worker revocation lag stays bounded and
    cache misses don't pile up at a single second boundary.
    """
    if not key or token is None:
        return
    expires = time.time() + (ttl if ttl is not None else _pick_ttl())
    token_id = str(getattr(token, "id", "") or "")
    with _lock:
        _cache[key] = (expires, token)
        _cache.move_to_end(key)
        if token_id:
            _by_token_id.setdefault(token_id, set()).add(key)
        while len(_cache) > _MAX_ENTRIES:
            old_key, (_, old_token) = _cache.popitem(last=False)
            _unmap_locked(old_key, old_token)


def invalidate(key: str) -> None:
    """Drop one cache entry (by its raw key)."""
    if not key:
        return
    with _lock:
        _drop_locked(key)


def invalidate_by_token_id(token_id: str) -> None:
    """Drop every entry pointing to a given backing token (all JWT forms).

    Call this from ``delete_access_token`` / rotation paths so a same-worker
    logout doesn't leave a stale principal in cache.
    """
    if not token_id:
        return
    with _lock:
        keys = _by_token_id.pop(token_id, set())
        for key in keys:
            _cache.pop(key, None)


def clear() -> None:
    """Drop everything. Used by the bulk revocation paths (e.g. "log me out
    everywhere") where tracking individual keys isn't worth the complexity.
    """
    with _lock:
        _cache.clear()
        _by_token_id.clear()


def stats() -> dict[str, int]:
    with _lock:
        return {
            "entries": len(_cache),
            "token_ids_tracked": len(_by_token_id),
            "max_entries": _MAX_ENTRIES,
        }


# ---------------------------------------------------------------------------
# Private helpers (assume ``_lock`` is already held)
# ---------------------------------------------------------------------------


def _drop_locked(key: str) -> None:
    hit = _cache.pop(key, None)
    if not hit:
        return
    _, token = hit
    _unmap_locked(key, token)


def _unmap_locked(key: str, token: accessTokenOut) -> None:
    token_id = str(getattr(token, "id", "") or "")
    if not token_id:
        return
    keys = _by_token_id.get(token_id)
    if not keys:
        return
    keys.discard(key)
    if not keys:
        _by_token_id.pop(token_id, None)
