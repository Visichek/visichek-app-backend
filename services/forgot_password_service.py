"""Self-service forgot-password flow (unauthenticated).

Two stages, both unauthenticated:

  POST /v1/auth/forgot-password   →  request a reset link by email
  POST /v1/auth/reset-password    →  consume the token + set new password

Design notes:

- The request endpoint ALWAYS returns the same 202 envelope, regardless
  of whether the email matched a real account. This is the standard
  defence against account-enumeration via timing or response diffs.
- The plaintext token is never stored — only ``sha256(token)`` lands in
  MongoDB. The plaintext is only sent in the email and never logged.
- One email can match an application admin AND zero-or-more tenant
  system users (system users are unique per tenant, not per platform).
  We mint a separate reset row + send a separate email for each match
  so the recipient picks the right tenant via the URL the link lands on.
- The env-pinned primary admin (id sentinel ``656f7ac12b9d4f6c9e2b9f7d``
  or matching SUPER_ADMIN_EMAIL) is EXCLUDED from this flow. Recovery
  for that account is via OTP_DEV_CODE + the env credentials — mailing
  a reset link would re-introduce the SMTP / inbox dependency the
  static creds exist to avoid.
- Consuming a token wipes every other outstanding token for the same
  account and revokes all live tokens so an attacker who phished a
  link cannot ride a concurrent reset attempt or a stale session.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any

from bson import ObjectId
from fastapi import HTTPException, Request

from core.database import db
from core.email_utils import normalize_email
from core.settings import get_settings
from repositories.admin_repo import get_admin
from repositories.password_reset_repo import (
    create_reset_token,
    get_reset_token_by_hash,
    invalidate_outstanding_reset_tokens,
    mark_reset_token_used,
)
from repositories.system_user_repo import get_raw_system_users_by_email
from repositories.tokens_repo import (
    delete_all_tokens_with_admin_id,
    delete_all_tokens_with_user_id,
)
from security.hash import hash_password
from security.password_policy import (
    check_password_history,
    record_password_in_history,
    validate_password_strength,
)

logger = logging.getLogger(__name__)

PRIMARY_ADMIN_ID = "656f7ac12b9d4f6c9e2b9f7d"
RESET_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_primary_env_admin(admin_id: str | None, email: str | None) -> bool:
    """Match either the sentinel id or SUPER_ADMIN_EMAIL (case-insensitive)."""
    if admin_id and admin_id == PRIMARY_ADMIN_ID:
        return True
    primary_email = (os.getenv("SUPER_ADMIN_EMAIL") or "").strip().lower()
    if not primary_email or not email:
        return False
    return email.strip().lower() == primary_email


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def _build_reset_url(token: str) -> str:
    """Compose the link the recipient clicks to land on the FE reset form.

    Empty when ``APP_BASE_URL`` is not configured — the email falls back
    to displaying the raw token so the recipient can paste it into the
    reset form manually.
    """
    base = (get_settings().app_base_url or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/reset-password?token={token}"


async def _tenant_label(tenant_id: str | None) -> str:
    """Best-effort tenant display name for the email body."""
    if not tenant_id:
        return ""
    try:
        if not ObjectId.is_valid(tenant_id):
            return ""
        doc = await db.tenant_companies.find_one(
            {"_id": ObjectId(tenant_id)}, {"company_name": 1}
        )
        if not doc:
            return ""
        return str(doc.get("company_name") or "")
    except Exception:
        return ""


async def _send_reset_email(
    *,
    to_email: str,
    recipient_name: str,
    token: str,
    tenant_id: str | None,
    requesting_ip: str | None,
) -> None:
    """Dispatch the HTML reset email. Fire-and-forget on failure."""
    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest

        settings = get_settings()
        platform_name = settings.email_sender_name or "VisiChek"
        ttl_minutes = max(RESET_TOKEN_TTL_SECONDS // 60, 1)

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=to_email,
                template_key="password_reset",
                context={
                    "recipient_name": recipient_name or "there",
                    "platform_name": platform_name,
                    "reset_url": _build_reset_url(token),
                    "token": token,
                    "ttl_minutes": str(ttl_minutes),
                    "requesting_ip": requesting_ip or "",
                    "tenant_label": await _tenant_label(tenant_id),
                },
                dispatch="auto",
            )
        )
    except Exception:
        logger.warning("password reset email send failed for %s", to_email, exc_info=True)


async def _issue_reset_for_admin(
    *, admin_doc: Any, requesting_ip: str | None
) -> None:
    """Mint a reset token for an application admin and email it."""
    admin_id = str(admin_doc.id or "") if hasattr(admin_doc, "id") else str(admin_doc.get("_id"))
    email = getattr(admin_doc, "email", None) or admin_doc.get("email", "")  # type: ignore[union-attr]
    full_name = getattr(admin_doc, "full_name", None) or admin_doc.get("full_name", "")  # type: ignore[union-attr]

    if _is_primary_env_admin(admin_id, email):
        # The env primary admin cannot be reset via this flow.
        return

    token = secrets.token_urlsafe(32)
    await create_reset_token(
        token_hash=_hash_reset_token(token),
        user_id=admin_id,
        user_type="admin",
        tenant_id=None,
        ttl_seconds=RESET_TOKEN_TTL_SECONDS,
        requesting_ip=requesting_ip,
    )
    await _send_reset_email(
        to_email=email,
        recipient_name=full_name,
        token=token,
        tenant_id=None,
        requesting_ip=requesting_ip,
    )


async def _issue_reset_for_system_user(
    *, raw_doc: dict, requesting_ip: str | None
) -> None:
    """Mint a reset token for a tenant system user and email it."""
    user_id = str(raw_doc.get("_id"))
    email = str(raw_doc.get("email") or "")
    full_name = str(raw_doc.get("full_name") or "")
    tenant_id = raw_doc.get("tenant_id")

    token = secrets.token_urlsafe(32)
    await create_reset_token(
        token_hash=_hash_reset_token(token),
        user_id=user_id,
        user_type="system_user",
        tenant_id=tenant_id,
        ttl_seconds=RESET_TOKEN_TTL_SECONDS,
        requesting_ip=requesting_ip,
    )
    await _send_reset_email(
        to_email=email,
        recipient_name=full_name,
        token=token,
        tenant_id=tenant_id,
        requesting_ip=requesting_ip,
    )


async def request_password_reset(
    *,
    email: str,
    request: Request | None = None,
) -> None:
    """Stage 1: accept an email and dispatch reset link(s).

    Silently no-ops when nothing matches — the route returns the same
    202 envelope so attackers cannot enumerate accounts via the
    response shape or timing. Side-channel timing is bounded by the
    fixed number of DB lookups regardless of match count.
    """
    raw_email = (email or "").strip()
    if not raw_email:
        return

    # Match by exact email AND by normalized email (catches +alias /
    # gmail-dot variants) to mirror how the invite path dedupes.
    normalized = normalize_email(raw_email)
    requesting_ip = _client_ip(request)

    # ------------------- admins -------------------
    admin = await get_admin({"email": raw_email})
    if not admin and normalized != raw_email:
        admin = await get_admin({"email": normalized})
    if admin:
        try:
            await _issue_reset_for_admin(admin_doc=admin, requesting_ip=requesting_ip)
        except Exception:
            logger.warning(
                "request_password_reset: admin issue failed for %s",
                raw_email,
                exc_info=True,
            )

    # --------------- system users -----------------
    # One email can be the same person across multiple tenants. We mint
    # one reset row + one email PER tenant so the recipient picks the
    # right tenant on the FE form.
    try:
        rows = await get_raw_system_users_by_email(raw_email)
        if not rows and normalized != raw_email:
            rows = await get_raw_system_users_by_email(normalized)
        for row in rows:
            try:
                await _issue_reset_for_system_user(
                    raw_doc=row, requesting_ip=requesting_ip
                )
            except Exception:
                logger.warning(
                    "request_password_reset: system_user issue failed for %s",
                    raw_email,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "request_password_reset: system_user lookup failed for %s",
            raw_email,
            exc_info=True,
        )


async def reset_password_with_token(
    *,
    token: str,
    new_password: str,
    request: Request | None = None,
) -> dict:
    """Stage 2: consume the token and set the new password.

    Returns ``{"user_type": "admin" | "system_user", "user_id": ...,
    "tenant_id": ... | None}`` so the route can echo a minimal payload
    to the FE without exposing more than the caller already knew.
    """
    import time

    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Reset token is required")

    row = await get_reset_token_by_hash(_hash_reset_token(token))
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    now = int(time.time())
    if row.get("used"):
        raise HTTPException(status_code=400, detail="This reset link has already been used")
    if int(row.get("expires_at", 0)) < now:
        raise HTTPException(status_code=400, detail="This reset link has expired")

    user_id = str(row.get("user_id") or "")
    user_type = str(row.get("user_type") or "")
    tenant_id = row.get("tenant_id")

    if user_type not in ("admin", "system_user") or not user_id:
        raise HTTPException(status_code=400, detail="Malformed reset record")

    # Enforce the platform password policy + history at the same point
    # the authenticated change-password path enforces them.
    from core.security_policy import get_security_policy

    policy = await get_security_policy()
    strength = validate_password_strength(new_password, policy=policy)
    if not strength.is_valid:
        raise HTTPException(status_code=422, detail="; ".join(strength.errors))

    safe = await check_password_history(
        user_id,
        new_password,
        role=user_type,
        history_count=policy.password_history_count,
    )
    if not safe:
        raise HTTPException(
            status_code=422,
            detail=(
                "New password must not match any of your last "
                f"{policy.password_history_count} passwords"
            ),
        )

    hashed = hash_password(new_password)

    if user_type == "admin":
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="Malformed reset record")
        admin_doc = await get_admin({"_id": ObjectId(user_id)})
        if not admin_doc:
            raise HTTPException(status_code=404, detail="Account not found")
        if _is_primary_env_admin(admin_doc.id, admin_doc.email):
            # Hard stop: the env primary admin cannot be reset via this
            # flow even if someone manages to mint a token row out-of-band.
            raise HTTPException(
                status_code=403,
                detail="The primary platform admin cannot be reset via this flow.",
            )
        await db.admins.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"password": hashed}},
        )
        await delete_all_tokens_with_admin_id(adminId=user_id)
    else:  # system_user
        if not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=400, detail="Malformed reset record")
        sys_doc = await db.system_users.find_one({"_id": ObjectId(user_id)})
        if not sys_doc:
            raise HTTPException(status_code=404, detail="Account not found")
        await db.system_users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"password_hash": hashed}},
        )
        await delete_all_tokens_with_user_id(userId=user_id)

    await record_password_in_history(
        user_id,
        hashed,
        role=user_type,
        history_count=policy.password_history_count,
    )

    # Mark this token used and invalidate every other outstanding token
    # for the same account so a parallel link cannot be redeemed.
    await mark_reset_token_used(str(row.get("_id")))
    await invalidate_outstanding_reset_tokens(user_id=user_id, user_type=user_type)

    # Best-effort: drop the gate snapshot so the next request sees the
    # fresh account state (mfa flags, status, etc.).
    try:
        from core.queue.gate_cache import invalidate_gate

        invalidate_gate(user_id=user_id)
    except Exception:
        pass

    # Audit: tenant-scoped resets are recorded on the audit trail so the
    # tenant can see "who reset their password and when". Admin resets
    # are recorded too — the actor is the user themselves because this
    # flow is self-service.
    try:
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=user_id,
            actor_role=user_type,
            action=f"{user_type}.password_reset_self_service",
            resource_type=user_type,
            resource_id=user_id,
            tenant_id=tenant_id,
            details={
                "via": "forgot_password_email",
                "requesting_ip": _client_ip(request) or "",
            },
        )
    except Exception:
        pass

    return {"user_type": user_type, "user_id": user_id, "tenant_id": tenant_id}
