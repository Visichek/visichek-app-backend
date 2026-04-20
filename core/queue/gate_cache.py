"""Smart gate-check cache.

Account-status + permission checks run on every authenticated request.
Once the JWT is cached in-process by :mod:`core.token_cache`, the
dominant cost is the DB lookup for the admin / user / system-user row.

This module caches that row (as a dict snapshot) in Redis keyed by
(role, user_id). The security deps serve the cached snapshot and
enqueue a background ``gate.refresh`` task so the next request sees an
up-to-date copy without paying the DB cost on the hot path.

Invariants:

* Snapshot TTL is short (``GATE_TTL_SECONDS``) so even in the absence of
  explicit invalidation, account changes propagate within one window.
* Explicit :func:`invalidate_gate` on admin / user / system-user mutation
  paths keeps revocation fast.
* Cache holds a JSON dict with ``{account_type, snapshot}`` — the
  security dep rehydrates the appropriate Pydantic ``*Out`` model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, cast

from bson import ObjectId

from core.queue.manager import QueueManager
from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

GATE_TTL_SECONDS = 300  # 5 minutes
_GATE_PREFIX = "gate"


def _gate_key(role: str, user_id: str) -> str:
    return f"{_GATE_PREFIX}:{role}:{user_id}"


async def _load_gate_from_db(user_id: str, role: str) -> Optional[dict[str, Any]]:
    """Run the DB lookup the legacy sync dep used to perform.

    Returns ``None`` when the account does not exist. Payload is the
    ``model_dump(mode="json")`` of the corresponding ``*Out`` schema so
    it round-trips cleanly through JSON.
    """
    if role == "admin":
        from services.admin_service import retrieve_admin_by_admin_id

        admin = await retrieve_admin_by_admin_id(id=user_id)
        if not admin:
            return None
        return {
            "account_type": "admin",
            "snapshot": admin.model_dump(mode="json", by_alias=True),
        }

    if role == "user":
        from services.user_service import retrieve_user_by_user_id

        user = await retrieve_user_by_user_id(id=user_id)
        if not user:
            return None
        return {
            "account_type": "user",
            "snapshot": user.model_dump(mode="json", by_alias=True),
        }

    # System user roles (tenant users).
    from repositories.system_user_repo import get_system_user

    try:
        oid = ObjectId(user_id)
    except Exception:
        return None

    system_user = await get_system_user({"_id": oid})
    if not system_user:
        return None
    return {
        "account_type": "system_user",
        "role": role,
        "snapshot": system_user.model_dump(mode="json", by_alias=True),
    }


async def resolve_gate(user_id: str, role: str) -> Optional[dict[str, Any]]:
    """Return the cached gate snapshot, refreshing asynchronously on hit.

    Cache miss = sync DB load + populate.
    Cache hit  = serve cached + enqueue ``gate.refresh`` for the next hit.
    """
    key = _gate_key(role, user_id)

    try:
        raw = cast(Optional[str], cache_db.get(key))
    except Exception:
        raw = None

    if raw:
        cached: Optional[dict[str, Any]]
        try:
            cached = json.loads(raw)
        except Exception:
            cached = None

        if cached:
            try:
                QueueManager.get_instance().enqueue(
                    task_key="gate.refresh",
                    payload={"user_id": user_id, "role": role},
                )
            except Exception:
                logger.warning(
                    "gate.refresh enqueue failed for user_id=%s role=%s",
                    user_id,
                    role,
                    exc_info=True,
                )
            return cached

    snapshot = await _load_gate_from_db(user_id=user_id, role=role)
    if snapshot is None:
        return None

    try:
        cache_db.setex(key, GATE_TTL_SECONDS, json.dumps(snapshot))
    except Exception:
        logger.warning("gate cache setex failed for key=%s", key, exc_info=True)

    return snapshot


async def refresh_gate_state(user_id: str, role: str) -> None:
    """Worker entry point for the ``gate.refresh`` task."""
    snapshot = await _load_gate_from_db(user_id=user_id, role=role)
    key = _gate_key(role, user_id)
    try:
        if snapshot is None:
            cache_db.delete(key)
            return
        cache_db.setex(key, GATE_TTL_SECONDS, json.dumps(snapshot))
    except Exception:
        logger.warning("refresh_gate_state failed for key=%s", key, exc_info=True)


def invalidate_gate(user_id: str, role: Optional[str] = None) -> None:
    """Drop the cached gate snapshot(s) for ``user_id``.

    Call from mutation paths (account update, password change, account
    deletion, token revocation of a specific principal) so the next
    request goes through a fresh sync check. Failures are swallowed —
    stale state self-corrects at the TTL.
    """
    try:
        if role:
            cache_db.delete(_gate_key(role, user_id))
            return
        pattern = f"{_GATE_PREFIX}:*:{user_id}"
        keys: list[str] = [str(k) for k in cache_db.scan_iter(match=pattern, count=100)]
        if keys:
            cache_db.delete(*keys)
    except Exception:
        logger.warning(
            "invalidate_gate failed for user_id=%s role=%s",
            user_id,
            role,
            exc_info=True,
        )
