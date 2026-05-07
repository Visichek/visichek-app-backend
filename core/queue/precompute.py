"""Precomputed-read pipeline.

Heavy GET payloads (dashboards, tenant-scoped list endpoints, rollups)
are precomputed off the request path by a dedicated worker and stored
in Redis under ``precomputed:{scope}:{resource}``. Route handlers read
from there first via :func:`get_or_compute`; on miss they fall through
to the registered loader and populate the cache before returning.

Scope types:

* ``tenant`` — one entry per tenant. The loader receives ``tenant_id``.
  Used by /v1/departments, /v1/dashboard/stats, etc.
* ``global`` — one entry for everyone. The loader receives ``""``.
  Used by /v1/plans (public plan listings), platform-wide aggregates.

Active-tenant detection:

Every authenticated request writes a short-lived heartbeat to Redis
under ``active:tenant:{tenant_id}``. The scheduler's
``precompute.fanout_active_tenants`` task scans this key pattern and
enqueues a precompute job per (active tenant × registered resource).
A tenant with zero live users therefore costs zero precompute work.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, cast

from bson import ObjectId

from core.queue.manager import QueueManager
from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

PRECOMPUTE_TTL_SECONDS = 300  # 5 minutes — refreshed by fanout well before expiry
ACTIVE_TENANT_TTL_SECONDS = 300  # 5 minutes — marks a tenant as "live"
ACTIVE_USER_TTL_SECONDS = 300  # 5 minutes — marks a user as "live"
DIRTY_TTL_SECONDS = 5  # Scope-level "stale write in flight" marker

_PRECOMPUTE_PREFIX = "precomputed"
_ACTIVE_TENANT_PREFIX = "active:tenant"
_ACTIVE_USER_PREFIX = "active:user"
_DIRTY_PREFIX = "dirty"
_DIRTY_GLOBAL = "global"


PrecomputeLoader = Callable[[str], Awaitable[Any]]


class PrecomputeScope(str, Enum):
    TENANT = "tenant"
    GLOBAL = "global"
    USER = "user"


_PRECOMPUTE_REGISTRY: dict[str, tuple[PrecomputeLoader, PrecomputeScope]] = {}


def register_precompute(
    resource: str, scope: PrecomputeScope = PrecomputeScope.TENANT
) -> Callable[[PrecomputeLoader], PrecomputeLoader]:
    """Register a loader for a named resource.

    The loader receives the tenant_id (or "" for global scope) and
    returns any JSON-serialisable payload. Pydantic models are
    serialised via ``_json_default``.
    """

    def decorator(func: PrecomputeLoader) -> PrecomputeLoader:
        if resource in _PRECOMPUTE_REGISTRY:
            raise ValueError(f"Precompute resource '{resource}' already registered")
        _PRECOMPUTE_REGISTRY[resource] = (func, scope)
        return func

    return decorator


def list_registered_resources() -> list[str]:
    return sorted(_PRECOMPUTE_REGISTRY.keys())


def _json_default(obj: Any) -> Any:
    """JSON fallback for payloads containing Pydantic models / BSON ids."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def _precompute_key(scope_key: str, resource: str) -> str:
    return f"{_PRECOMPUTE_PREFIX}:{scope_key}:{resource}"


def get_resource_scope(resource: str) -> Optional[PrecomputeScope]:
    entry = _PRECOMPUTE_REGISTRY.get(resource)
    if not entry:
        return None
    return entry[1]


# ─── Dirty-scope markers ──────────────────────────────────────────────
#
# Set immediately on any write enqueue so reads in the worker-commit gap
# bypass HTTP/precompute caches (which would otherwise lock in pre-write
# state for up to 60s).


def _dirty_key(scope: str) -> str:
    return f"{_DIRTY_PREFIX}:{scope}"


def mark_scope_dirty(scope: str, ttl: int = DIRTY_TTL_SECONDS) -> None:
    """Mark a request scope as having a write-in-flight.

    ``scope`` mirrors the HttpCacheMiddleware scope keys:
    ``t:{tenant_id}``, ``adm:{admin_id}``, ``usr:{user_id}``, or the
    sentinel ``global`` for writes that touch a globally-scoped resource.
    """
    if not scope:
        return
    try:
        cache_db.setex(_dirty_key(scope), ttl, "1")
    except Exception:
        logger.debug("mark_scope_dirty failed for %s", scope, exc_info=True)


def is_scope_dirty(scope: str) -> bool:
    """Return True if ``scope`` (or the global sentinel) is dirty."""
    if not scope:
        scope = _DIRTY_GLOBAL
    try:
        if cache_db.get(_dirty_key(scope)):
            return True
        if scope != _DIRTY_GLOBAL and cache_db.get(_dirty_key(_DIRTY_GLOBAL)):
            return True
    except Exception:
        return False
    return False


def delete_precompute(
    resource: str,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Delete the cached precompute payload for ``resource``.

    Scope is resolved from the registry entry so callers don't need to
    know how each resource is keyed. Silently no-ops for unknown
    resources or when the required scope id is missing.
    """
    scope = get_resource_scope(resource)
    if scope is None:
        return
    if scope is PrecomputeScope.TENANT:
        if not tenant_id:
            return
        scope_key = f"tenant:{tenant_id}"
    elif scope is PrecomputeScope.USER:
        if not user_id:
            return
        scope_key = f"tenant:{tenant_id or '_'}:user:{user_id}"
    else:
        scope_key = "global"
    try:
        cache_db.delete(_precompute_key(scope_key, resource))
    except Exception:
        logger.debug(
            "delete_precompute failed resource=%s tenant=%s user=%s",
            resource,
            tenant_id,
            user_id,
            exc_info=True,
        )


def mark_tenant_active(tenant_id: Optional[str]) -> None:
    """Record that ``tenant_id`` had a live authenticated request just now.

    Called from token resolution so the worker's fanout step can target
    only tenants with real traffic. Never raises — cache failures are
    acceptable (fanout simply skips the tenant for this cycle).
    """
    if not tenant_id:
        return
    try:
        cache_db.setex(
            f"{_ACTIVE_TENANT_PREFIX}:{tenant_id}",
            ACTIVE_TENANT_TTL_SECONDS,
            str(int(time.time())),
        )
    except Exception:
        logger.debug("mark_tenant_active failed", exc_info=True)


def iter_active_tenant_ids() -> list[str]:
    """Return the set of tenant_ids currently considered active."""
    try:
        keys = cast(
            list[str],
            list(cache_db.scan_iter(match=f"{_ACTIVE_TENANT_PREFIX}:*", count=200)),
        )
    except Exception:
        logger.warning("iter_active_tenant_ids scan failed", exc_info=True)
        return []
    prefix = f"{_ACTIVE_TENANT_PREFIX}:"
    out: list[str] = []
    for key in keys:
        key_str = key if isinstance(key, str) else str(key)
        if key_str.startswith(prefix):
            out.append(key_str[len(prefix) :])
    return out


def mark_user_active(user_id: Optional[str], tenant_id: Optional[str] = None) -> None:
    """Record that ``user_id`` had a live authenticated request just now.

    The heartbeat value stores the tenant_id so the fanout step can key
    per-user precompute entries under ``tenant:{tenant_id}:user:{user_id}``
    and route the refresh task to the right tenant context.
    """
    if not user_id:
        return
    try:
        cache_db.setex(
            f"{_ACTIVE_USER_PREFIX}:{user_id}",
            ACTIVE_USER_TTL_SECONDS,
            tenant_id or "",
        )
    except Exception:
        logger.debug("mark_user_active failed", exc_info=True)


def iter_active_users() -> list[tuple[str, str]]:
    """Return ``[(user_id, tenant_id), ...]`` for every live user.

    ``tenant_id`` is the empty string for application admins / users who
    have no tenant scope.
    """
    try:
        keys = cast(
            list[str],
            list(cache_db.scan_iter(match=f"{_ACTIVE_USER_PREFIX}:*", count=200)),
        )
    except Exception:
        logger.warning("iter_active_users scan failed", exc_info=True)
        return []
    prefix = f"{_ACTIVE_USER_PREFIX}:"
    out: list[tuple[str, str]] = []
    for key in keys:
        key_str = key if isinstance(key, str) else str(key)
        if not key_str.startswith(prefix):
            continue
        user_id = key_str[len(prefix) :]
        try:
            tenant_id = cast(Optional[str], cache_db.get(key_str)) or ""
        except Exception:
            tenant_id = ""
        out.append((user_id, tenant_id))
    return out


async def get_or_compute(
    *,
    scope_key: str,
    resource: str,
    ttl: int,
    loader: Callable[[], Awaitable[Any]],
) -> Any:
    """Return the precomputed payload for ``scope_key``/``resource``.

    * HIT  → deserialise and return the cached value.
    * MISS → run ``loader()``, cache the result, return it.

    Unlike :class:`HttpCacheMiddleware`, this does not serve full HTTP
    responses — it caches the raw payload the route handler returns.
    Keeping the cached shape at the service-layer return type means
    routes stay unchanged whether or not a hit is served.
    """
    redis_key = _precompute_key(scope_key, resource)
    dirty_scope = _scope_key_to_dirty_scope(scope_key)
    bypass = is_scope_dirty(dirty_scope) if dirty_scope else False

    if not bypass:
        try:
            raw = cast(Optional[str], cache_db.get(redis_key))
        except Exception:
            raw = None

        if raw:
            try:
                return json.loads(raw)
            except Exception:
                logger.warning("precompute cache JSON decode failed for %s", redis_key)

    result = await loader()
    if not bypass:
        # When the scope is dirty a write is committing right now; the
        # loader may be reading pre-write state. Skip SETEX so a fresh
        # value isn't overwritten by stale data — the precompute worker
        # will populate the cache with post-commit state shortly.
        try:
            cache_db.setex(redis_key, ttl, json.dumps(result, default=_json_default))
        except Exception:
            logger.warning(
                "precompute cache setex failed for %s", redis_key, exc_info=True
            )
    return result


def _scope_key_to_dirty_scope(scope_key: str) -> str:
    """Map a precompute scope_key to the matching HttpCache dirty-scope.

    ``tenant:{tid}``           → ``t:{tid}``
    ``tenant:{tid}:user:{uid}`` → ``usr:{uid}``
    ``global``                 → ``global``
    """
    if scope_key.startswith("tenant:") and ":user:" in scope_key:
        return f"usr:{scope_key.rsplit(':', 1)[-1]}"
    if scope_key.startswith("tenant:"):
        return f"t:{scope_key.split(':', 1)[1]}"
    return _DIRTY_GLOBAL


async def run_precompute(
    tenant_id: str, resource: str, user_id: Optional[str] = None
) -> None:
    """Worker entry point: recompute ``resource`` for the scope.

    * TENANT scope  — ``tenant_id`` drives the loader + key.
    * GLOBAL scope  — ``tenant_id`` / ``user_id`` are ignored.
    * USER   scope  — ``user_id`` drives the loader; ``tenant_id``
      becomes part of the key so per-user entries are isolated from
      collisions across tenants.
    """
    entry = _PRECOMPUTE_REGISTRY.get(resource)
    if not entry:
        logger.warning("precompute: unknown resource %s", resource)
        return

    loader, scope = entry
    if scope is PrecomputeScope.TENANT:
        scope_key = f"tenant:{tenant_id}"
        result = await loader(tenant_id)
    elif scope is PrecomputeScope.USER:
        if not user_id:
            logger.warning(
                "precompute: user-scope resource %s missing user_id", resource
            )
            return
        scope_key = f"tenant:{tenant_id or '_'}:user:{user_id}"
        result = await loader(user_id)
    else:
        scope_key = "global"
        result = await loader("")

    redis_key = _precompute_key(scope_key, resource)
    try:
        cache_db.setex(
            redis_key,
            PRECOMPUTE_TTL_SECONDS,
            json.dumps(result, default=_json_default),
        )
    except Exception:
        logger.warning("run_precompute write failed for %s", redis_key, exc_info=True)


async def fanout_for_active_tenants() -> None:
    """Enqueue a precompute for every (active tenant × registered resource)
    and (active user × user-scoped resource).

    Global-scope resources fan out once per cycle regardless of activity.
    """
    if not _PRECOMPUTE_REGISTRY:
        return

    try:
        qm = QueueManager.get_instance()
    except RuntimeError:
        logger.warning("fanout_for_active_tenants: QueueManager not configured")
        return

    tenant_ids = iter_active_tenant_ids()
    active_users = iter_active_users()
    tenant_resources = [
        resource
        for resource, (_, scope) in _PRECOMPUTE_REGISTRY.items()
        if scope is PrecomputeScope.TENANT
    ]
    global_resources = [
        resource
        for resource, (_, scope) in _PRECOMPUTE_REGISTRY.items()
        if scope is PrecomputeScope.GLOBAL
    ]
    user_resources = [
        resource
        for resource, (_, scope) in _PRECOMPUTE_REGISTRY.items()
        if scope is PrecomputeScope.USER
    ]

    for tenant_id in tenant_ids:
        for resource in tenant_resources:
            try:
                qm.enqueue(
                    task_key="precompute.tenant_resource",
                    payload={"tenant_id": tenant_id, "resource": resource},
                )
            except Exception:
                logger.warning(
                    "fanout enqueue failed tenant=%s resource=%s",
                    tenant_id,
                    resource,
                    exc_info=True,
                )

    for resource in global_resources:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "fanout enqueue failed for global resource=%s",
                resource,
                exc_info=True,
            )

    for user_id, tenant_id in active_users:
        for resource in user_resources:
            try:
                qm.enqueue(
                    task_key="precompute.tenant_resource",
                    payload={
                        "tenant_id": tenant_id,
                        "resource": resource,
                        "user_id": user_id,
                    },
                )
            except Exception:
                logger.warning(
                    "fanout enqueue failed user=%s resource=%s",
                    user_id,
                    resource,
                    exc_info=True,
                )
