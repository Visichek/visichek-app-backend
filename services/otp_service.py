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


PRIMARY_ADMIN_ID = "656f7ac12b9d4f6c9e2b9f7d"


async def _is_primary_env_admin(user_id: str) -> bool:
    """True when ``user_id`` is the env-configured platform admin.

    The primary admin keeps using ``OTP_DEV_CODE`` for 2FA so on-call
    recovery never depends on email deliverability — every other admin
    must use the code we mail them.
    """
    if user_id == PRIMARY_ADMIN_ID:
        return True
    import os

    primary_email = (os.getenv("SUPER_ADMIN_EMAIL") or "").strip().lower()
    if not primary_email:
        return False
    try:
        from bson import ObjectId

        from core.database import db

        doc = await db.admins.find_one({"_id": ObjectId(user_id)}, {"email": 1})
        if not doc:
            return False
        return (doc.get("email") or "").strip().lower() == primary_email
    except Exception:
        return False


async def _send_admin_otp_email(user_id: str, code: str) -> None:
    """Mail the OTP code to an invited admin.

    Fire-and-forget: failures only log. The OTP challenge still exists
    in MongoDB so on-call can read it from the dashboard if the email
    pipeline is misconfigured. Skipped for the env primary admin (they
    use the static dev code).
    """
    try:
        if await _is_primary_env_admin(user_id):
            return

        from bson import ObjectId

        from core.database import db
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest

        doc = await db.admins.find_one(
            {"_id": ObjectId(user_id)}, {"email": 1, "full_name": 1}
        )
        if not doc or not doc.get("email"):
            return

        settings = get_settings()
        ttl_minutes = max(settings.otp_ttl_seconds // 60, 1)

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=doc["email"],
                template_key="admin_otp_code",
                context={
                    "recipient_name": doc.get("full_name") or "there",
                    "platform_name": settings.email_sender_name or "VisiChek",
                    "code": code,
                    "ttl_minutes": str(ttl_minutes),
                    "requesting_ip": "",
                },
                dispatch="auto",
            )
        )
    except Exception:
        logger.warning(
            "admin OTP email send failed for user_id=%s", user_id, exc_info=True
        )


async def create_otp_challenge(
    user_id: str,
    user_type: str,
    role: str,
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """Create a pending OTP challenge. Returns (challenge_id, raw_otp_code)."""
    settings = get_settings()

    # Force the env primary admin to keep using the static dev code even
    # in production so on-call recovery never depends on email delivery.
    if user_type == "admin" and await _is_primary_env_admin(user_id):
        code = settings.otp_dev_code
    elif settings.env != "production":
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

    # Mail invited admins their code (no-op for env primary admin and for
    # non-admin roles — system users get OTP via their own channel today).
    if user_type == "admin":
        await _send_admin_otp_email(user_id, code)

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
    """Check whether a user must complete 2FA.

    The platform admin owns the rule (``enforce_totp_for_admins`` /
    ``enforce_totp_for_tenant_users``); a tenant cannot opt itself in
    or out. Tenant users may *additionally* enable 2FA on their own
    account, in which case ``user.mfa_enabled`` triggers the prompt
    even when platform enforcement is off.
    """
    from core.security_policy import get_security_policy

    policy = await get_security_policy()

    if user_type == "admin":
        return policy.enforce_totp_for_admins

    from core.database import db
    from bson import ObjectId

    if ObjectId.is_valid(user_id):
        user = await db.system_users.find_one({"_id": ObjectId(user_id)})
    else:
        user = await db.system_users.find_one({"_id": user_id})

    if not user:
        # Platform-level enforcement still applies even if the user record
        # is unexpectedly missing.
        return policy.enforce_totp_for_tenant_users

    if policy.enforce_totp_for_tenant_users:
        return True

    return bool(user.get("mfa_enabled", False))
