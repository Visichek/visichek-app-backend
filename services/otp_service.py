from __future__ import annotations

import logging
import time

from fastapi import HTTPException

from core.settings import get_settings
from repositories.otp_repo import (
    create_otp_challenge as _create_challenge,
    get_otp_challenge as _get_challenge,
    increment_otp_attempts,
    mark_otp_used,
)
from security.hash import check_otp, generate_otp_code, hash_otp

logger = logging.getLogger(__name__)


async def create_otp_challenge(
    user_id: str,
    user_type: str,
    role: str,
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """Create a pending OTP challenge. Returns (challenge_id, raw_otp_code)."""
    settings = get_settings()

    if settings.env != "production":
        code = settings.otp_dev_code
    else:
        code = generate_otp_code()

    now = int(time.time())
    doc = {
        "user_id": user_id,
        "user_type": user_type,
        "role": role,
        "tenant_id": tenant_id,
        "otp_hash": hash_otp(code),
        "created_at": now,
        "expires_at": now + settings.otp_ttl_seconds,
        "attempts": 0,
        "max_attempts": settings.otp_max_attempts,
        "used": False,
    }
    result = await _create_challenge(doc)
    challenge_id = str(result["_id"])

    if settings.env != "production":
        logger.info("DEV OTP code for %s (%s): %s", user_type, user_id, code)

    return challenge_id, code


async def verify_otp_challenge(challenge_id: str, otp_code: str) -> dict:
    """Verify an OTP code against a pending challenge.

    Returns dict with user_id, user_type, role, tenant_id on success.
    Raises HTTPException on failure.
    """
    challenge = await _get_challenge(challenge_id)
    if not challenge:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP challenge")

    now = int(time.time())
    if challenge.get("used"):
        raise HTTPException(status_code=401, detail="OTP challenge already used")

    if challenge["expires_at"] < now:
        raise HTTPException(status_code=401, detail="OTP challenge has expired")

    if challenge["attempts"] >= challenge["max_attempts"]:
        raise HTTPException(
            status_code=429, detail="Too many OTP attempts. Please log in again."
        )

    await increment_otp_attempts(challenge_id)

    if not check_otp(otp_code, challenge["otp_hash"]):
        remaining = challenge["max_attempts"] - challenge["attempts"] - 1
        raise HTTPException(
            status_code=401,
            detail=f"Invalid OTP code. {remaining} attempt(s) remaining.",
        )

    await mark_otp_used(challenge_id)

    return {
        "user_id": challenge["user_id"],
        "user_type": challenge["user_type"],
        "role": challenge["role"],
        "tenant_id": challenge.get("tenant_id"),
    }


async def is_mfa_required(user_type: str, user_id: str) -> bool:
    """Check whether a user must complete 2FA."""
    if user_type == "admin":
        return True

    from core.database import db
    from bson import ObjectId

    if ObjectId.is_valid(user_id):
        user = await db.system_users.find_one({"_id": ObjectId(user_id)})
    else:
        user = await db.system_users.find_one({"_id": user_id})

    if not user:
        return False

    return bool(user.get("mfa_enabled", False))
