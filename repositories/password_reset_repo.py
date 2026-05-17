"""Storage layer for self-service password-reset tokens.

The plaintext token is never stored — only a sha256 hash. That way a
leak of the ``password_reset_tokens`` collection alone cannot be
weaponised to take over accounts; the attacker would also need the
plaintext from the email (which we never persist) or to brute-force
the SHA-256 of a 32-byte URL-safe token (intractable).

A row is created on every ``POST /v1/auth/forgot-password`` that
matches a real account. The consume path
(``POST /v1/auth/reset-password``) marks the row ``used=True`` after
the password change, so the same link cannot be replayed.
"""

from __future__ import annotations

import time

from bson import ObjectId

from core.database import db

COLLECTION = "password_reset_tokens"


async def create_reset_token(
    *,
    token_hash: str,
    user_id: str,
    user_type: str,
    tenant_id: str | None,
    ttl_seconds: int,
    requesting_ip: str | None,
) -> str:
    """Insert a new pending reset token. Returns the row id."""
    now = int(time.time())
    doc = {
        "token_hash": token_hash,
        "user_id": user_id,
        "user_type": user_type,
        "tenant_id": tenant_id,
        "created_at": now,
        "expires_at": now + ttl_seconds,
        "used": False,
        "used_at": None,
        "requesting_ip": requesting_ip,
    }
    result = await db[COLLECTION].insert_one(doc)
    return str(result.inserted_id)


async def get_reset_token_by_hash(token_hash: str) -> dict | None:
    """Look up a reset-token row by its sha256 hash.

    Returns None if no row matches; the service layer is responsible
    for checking ``used`` / ``expires_at`` after retrieval.
    """
    return await db[COLLECTION].find_one({"token_hash": token_hash})


async def mark_reset_token_used(token_id: str) -> None:
    """Idempotently mark a reset-token row consumed."""
    await db[COLLECTION].update_one(
        {"_id": ObjectId(token_id)},
        {"$set": {"used": True, "used_at": int(time.time())}},
    )


async def invalidate_outstanding_reset_tokens(user_id: str, user_type: str) -> int:
    """Mark every pending reset row for this user as used.

    Called after a successful password change so any other unused
    links already in flight (e.g. the user clicked "forgot password"
    twice) can no longer be redeemed. Returns the count invalidated.
    """
    result = await db[COLLECTION].update_many(
        {"user_id": user_id, "user_type": user_type, "used": False},
        {"$set": {"used": True, "used_at": int(time.time())}},
    )
    return int(getattr(result, "modified_count", 0) or 0)
