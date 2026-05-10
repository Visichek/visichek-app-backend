"""Idempotency-Key replay protection for unsafe POSTs.

Bulk endpoints (and any other unsafe operation) accept an
``Idempotency-Key`` header. The first request with a given key is
processed normally; the response is recorded. Subsequent requests
within the TTL with the same key replay the cached response instead of
re-running the action.

Security requirements (enforced here):

* The cache key is **scoped per actor** (admin/user id) so an attacker
  who learns an idempotency key from another tenant cannot replay it.
* The cache also includes the route path so the same key on a
  different endpoint doesn't collide.
* The body fingerprint is recorded; if a replay arrives with a
  different body under the same key we fail closed with
  ``IDEMPOTENCY_CONFLICT`` rather than silently returning the cached
  response. This blocks key-reuse attacks that try to substitute the
  payload after the fact.
* Keys must be 8..200 chars and printable ASCII to avoid header
  smuggling / control-character injection downstream.
* If Redis is unavailable, idempotency degrades to "no replay" (we
  process the request normally) — never to "always replay", because
  a stale replay is the more dangerous failure mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, cast

from core.errors import AppException, ErrorCode
from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h replay window
KEY_RE = re.compile(r"^[A-Za-z0-9._\-:+/=]{8,200}$")

_PREFIX = "idem"


@dataclass
class IdempotencyHit:
    response: Any
    status_code: int


def _conflict() -> AppException:
    return AppException(
        status_code=409,
        code=ErrorCode.VALIDATION_FAILED,
        message="Idempotency-Key replay with a different request body",
        details={"code": "IDEMPOTENCY_CONFLICT"},
    )


def _bad_key() -> AppException:
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Invalid Idempotency-Key header",
        details={"code": "IDEMPOTENCY_KEY_INVALID"},
    )


def _validate_key(raw: str) -> str:
    if not isinstance(raw, str) or not KEY_RE.match(raw):
        raise _bad_key()
    return raw


def _fingerprint_body(body: Any) -> str:
    if body is None:
        return "_"
    try:
        canonical = json.dumps(body, sort_keys=True, default=str)
    except TypeError:
        canonical = repr(body)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redis_key(*, key: str, scope: str, route: str) -> str:
    safe_route = re.sub(r"[^A-Za-z0-9_\-:/]", "_", route)[:128]
    safe_scope = re.sub(r"[^A-Za-z0-9_\-:]", "_", scope)[:128]
    return f"{_PREFIX}:{safe_scope}:{safe_route}:{key}"


def check_idempotency(
    *,
    key: Optional[str],
    scope: str,
    route: str,
    body: Any,
) -> Optional[IdempotencyHit]:
    """Look up a prior response. Returns ``None`` if first-seen.

    Raises ``IDEMPOTENCY_CONFLICT`` if the same key replays with a
    different body fingerprint.
    """
    if not key:
        return None
    if not scope:
        # No actor context → refuse to replay rather than risk leaking
        # one user's response to another caller.
        return None
    valid_key = _validate_key(key)
    rkey = _redis_key(key=valid_key, scope=scope, route=route)
    body_fp = _fingerprint_body(body)
    try:
        raw = cast(Optional[str], cache_db.get(rkey))
    except Exception:
        logger.debug("idempotency get failed for %s", rkey, exc_info=True)
        return None
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    if record.get("body_fp") != body_fp:
        raise _conflict()
    return IdempotencyHit(
        response=record.get("response"),
        status_code=int(record.get("status_code", 200)),
    )


def store_idempotency(
    *,
    key: Optional[str],
    scope: str,
    route: str,
    body: Any,
    response: Any,
    status_code: int = 200,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> None:
    if not key or not scope:
        return
    try:
        valid_key = _validate_key(key)
    except AppException:
        return
    rkey = _redis_key(key=valid_key, scope=scope, route=route)
    record = {
        "body_fp": _fingerprint_body(body),
        "response": response,
        "status_code": status_code,
    }
    try:
        cache_db.setex(rkey, ttl, json.dumps(record, default=str))
    except Exception:
        logger.debug("idempotency setex failed for %s", rkey, exc_info=True)


def actor_scope(actor_id: Optional[str], actor_role: Optional[str]) -> str:
    """Build a scope key from a principal — ``role:user_id``.

    ``role`` participates so an attacker who somehow obtained the
    user_id of an admin can't replay against a system_user endpoint.
    """
    if not actor_id:
        return ""
    role = actor_role or "?"
    return f"{role}:{actor_id}"


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "IdempotencyHit",
    "check_idempotency",
    "store_idempotency",
    "actor_scope",
]
