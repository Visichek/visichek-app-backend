"""Per-id read-through cache for single-entity GETs.

The precompute pipeline covers paginated list views. This module covers
the other half of the hot path: ``GET /v1/<resource>/{id}`` — single-
entity lookups currently hit MongoDB on every request, which adds
~5–30ms of DB latency to detail views, comment threads, and any modal
that fetches a single record.

How it works
------------

* Routes call :func:`get_or_compute_entity` instead of the service
  directly. On hit the cached payload is returned (~1ms Redis GET);
  on miss the loader runs, populates the cache, and returns the result.
* Writers call :func:`invalidate_entity` on every mutation so the
  next read sees fresh state. The dispatcher in
  :mod:`core.queue.tasks` invokes this automatically using the
  resource_type / resource_id threaded through ``enqueue_write``.
* ``ENTITY_TTL_SECONDS`` is tight (60s) since the dirty marker /
  invalidate hook give us strong consistency on the hot path; the
  TTL is just defence against a missed invalidate.

Cache key shape: ``entity:{type}:{id}``. Type strings should match the
``resource_type`` argument used in :func:`enqueue_write` so writers
auto-invalidate the right key.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, cast

from bson import ObjectId

from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

ENTITY_TTL_SECONDS = 60  # 1 minute safety net; explicit invalidations dominate

_ENTITY_PREFIX = "entity"


def _entity_key(entity_type: str, entity_id: str) -> str:
    return f"{_ENTITY_PREFIX}:{entity_type}:{entity_id}"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, ObjectId):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


async def get_or_compute_entity(
    *,
    entity_type: str,
    entity_id: str,
    loader: Callable[[], Awaitable[Any]],
    ttl: int = ENTITY_TTL_SECONDS,
) -> Any:
    """Return the cached entity payload, falling through to ``loader`` on miss.

    The loader's return value must be JSON-serialisable (Pydantic models
    work via ``model_dump``). On miss the result is cached for ``ttl``
    seconds so subsequent identical reads hit Redis.
    """
    if not entity_type or not entity_id:
        return await loader()

    key = _entity_key(entity_type, entity_id)

    try:
        raw = cast(Optional[str], cache_db.get(key))
    except Exception:
        raw = None

    if raw:
        try:
            return json.loads(raw)
        except Exception:
            logger.warning("entity cache JSON decode failed for %s", key)

    result = await loader()
    try:
        cache_db.setex(key, ttl, json.dumps(result, default=_json_default))
    except Exception:
        logger.warning("entity cache setex failed for %s", key, exc_info=True)
    return result


def invalidate_entity(entity_type: str, entity_id: Optional[str]) -> None:
    """Drop the cached payload for ``entity_type:entity_id``.

    Called from the write dispatcher and any code path that bypasses the
    queue (e.g. internal service-to-service mutations). Safe to call
    with a missing id — no-op.
    """
    if not entity_type or not entity_id:
        return
    try:
        cache_db.delete(_entity_key(entity_type, entity_id))
    except Exception:
        logger.debug(
            "invalidate_entity failed type=%s id=%s",
            entity_type,
            entity_id,
            exc_info=True,
        )
