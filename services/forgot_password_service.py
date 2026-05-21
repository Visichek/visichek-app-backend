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
import time

from bson import ObjectId
from fastapi import HTTPException, Request

from core.database import db
from core.email_utils import normalize_email
from core.settings import get_settings
from repositories.admin_repo import get_admin
from repositories.password_reset_repo import (
    create_reset_selection,
    create_reset_token,
    get_reset_selection_by_hash,
    get_reset_token_by_hash,
    invalidate_outstanding_reset_tokens,
    mark_reset_selection_consumed,
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
# Step 1 (account lookup) → step 2 (send) window. Short-lived: the user
# is expected to pick an account immediately after entering their email.
SELECTION_TTL_SECONDS = 15 * 60  # 15 minutes


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

    Prefers ``APP_BASE_URL``. If that is unset we fall back to the first
    configured ``CORS_ORIGINS`` entry — the frontend origin the API
    already trusts — so the email is never silently linkless. Only when
    BOTH are empty do we return ``""`` (and log a warning), which the
    template treats as "no link to render".
    """
    settings = get_settings()
    base = (settings.app_base_url or "").rstrip("/")
    if not base:
        for origin in settings.cors_origins:
            candidate = (origin or "").strip().rstrip("/")
            if candidate:
                base = candidate
                break
    if not base:
        logger.warning(
            "password reset link omitted: neither APP_BASE_URL nor "
            "CORS_ORIGINS is configured — set APP_BASE_URL to the frontend "
            "base URL so reset emails contain a working link"
        )
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
        logger.warning(
            "password reset email send failed for %s", to_email, exc_info=True
        )


_ROLE_LABELS: dict[str, str] = {
    "admin": "Platform Administrator",
    "super_admin": "Tenant Super Admin",
    "dept_admin": "Department Admin",
    "receptionist": "Receptionist",
    "auditor": "Auditor",
    "security_officer": "Security Officer",
    "dpo": "Data Protection Officer",
}


def _role_label(role: str | None) -> str:
    return _ROLE_LABELS.get(str(role or ""), str(role or "").replace("_", " ").title())


async def _resolve_matching_accounts(
    raw_email: str,
) -> list[dict]:
    """Find every account that shares ``raw_email``.

    Returns server-side descriptors (with real ids) for one platform
    admin (if matched) plus one entry per tenant system_user row. The
    env-pinned primary admin is excluded — its recovery is OTP-based.

    Each descriptor: ``{user_type, user_id, tenant_id, email, full_name,
    role, type, tenant_name, label}``. ``type`` is ``"platform"`` for the
    application admin and ``"tenant"`` for system users; ``label`` /
    ``tenant_name`` are display-only fields for the selection UI.
    """
    normalized = normalize_email(raw_email)
    accounts: list[dict] = []

    # ------------------- application admin -------------------
    admin = await get_admin({"email": raw_email})
    if not admin and normalized != raw_email:
        admin = await get_admin({"email": normalized})
    if admin:
        admin_id = str(admin.id or "")
        admin_email = admin.email or raw_email
        if not _is_primary_env_admin(admin_id, admin_email):
            accounts.append(
                {
                    "user_type": "admin",
                    "user_id": admin_id,
                    "tenant_id": None,
                    "email": admin_email,
                    "full_name": admin.full_name or "",
                    "role": "admin",
                    "type": "platform",
                    "tenant_name": None,
                    "label": "Platform Administrator",
                }
            )

    # --------------- tenant system users -----------------
    # Same email can be the same person across multiple tenants — one
    # descriptor per tenant so the user picks which account(s) to reset.
    rows = await get_raw_system_users_by_email(raw_email)
    if not rows and normalized != raw_email:
        rows = await get_raw_system_users_by_email(normalized)
    for row in rows:
        tenant_id = row.get("tenant_id")
        tenant_name = await _tenant_label(tenant_id)
        role = row.get("role")
        accounts.append(
            {
                "user_type": "system_user",
                "user_id": str(row.get("_id")),
                "tenant_id": tenant_id,
                "email": str(row.get("email") or raw_email),
                "full_name": str(row.get("full_name") or ""),
                "role": role,
                "type": "tenant",
                "tenant_name": tenant_name or "",
                "label": tenant_name or "Your organization",
            }
        )

    return accounts


def _public_account_view(ref: str, account: dict) -> dict:
    """Project a server-side account descriptor into the client shape.

    Only opaque, display-safe fields cross the wire — never the raw
    ``user_id``. The frontend renders this list so the user can tell a
    platform login apart from each tenant login.
    """
    return {
        "account_ref": ref,
        "type": account["type"],
        "label": account["label"],
        "email": account["email"],
        "tenant_id": account.get("tenant_id"),
        "tenant_name": account.get("tenant_name"),
        "role": account.get("role"),
        "role_label": _role_label(account.get("role")),
    }


async def lookup_reset_accounts(
    *,
    email: str,
    request: Request | None = None,
) -> dict:
    """Step 1: resolve every account sharing an email (no email sent).

    Returns ``{selection_token, accounts, expires_in}``. ``accounts`` is
    the display list the frontend renders so the user can pick which
    login(s) to reset; ``selection_token`` is echoed back to
    ``send_reset_for_selection`` in step 2. When nothing matches we still
    return a (single-use, empty) selection so the response shape is
    uniform.

    NOTE: returning the matched-account list is an intentional product
    decision — it trades the old "uniform 202 regardless of match"
    anti-enumeration guarantee for a usable multi-account picker. Step 2
    is what actually sends mail, and it can only target accounts found
    here, always to the server-stored address.
    """
    raw_email = (email or "").strip()
    requesting_ip = _client_ip(request)

    accounts: list[dict] = []
    if raw_email:
        try:
            accounts = await _resolve_matching_accounts(raw_email)
        except Exception:
            logger.warning(
                "lookup_reset_accounts: resolution failed for %s",
                raw_email,
                exc_info=True,
            )
            accounts = []

    # Attach an opaque per-account ref the client uses to choose in step 2.
    for account in accounts:
        account["ref"] = secrets.token_urlsafe(16)

    selection_token = secrets.token_urlsafe(32)
    await create_reset_selection(
        selection_token_hash=_hash_reset_token(selection_token),
        email=raw_email,
        accounts=accounts,
        ttl_seconds=SELECTION_TTL_SECONDS,
        requesting_ip=requesting_ip,
    )

    return {
        "selection_token": selection_token,
        "accounts": [_public_account_view(a["ref"], a) for a in accounts],
        "expires_in": SELECTION_TTL_SECONDS,
    }


async def send_reset_for_selection(
    *,
    selection_token: str,
    account_refs: list[str],
    request: Request | None = None,
) -> dict:
    """Step 2: email a single-use reset link for each chosen account.

    Looks up the selection minted by step 1, validates it is neither
    expired nor already consumed, then mints one reset token + sends one
    link per selected ref. Refs not in the original selection are
    ignored. Marks the selection consumed (single-use) so the same token
    can't be replayed to spam mail. Returns ``{sent}``.
    """
    requesting_ip = _client_ip(request)

    if not selection_token or not isinstance(selection_token, str):
        raise HTTPException(status_code=400, detail="Selection token is required")

    row = await get_reset_selection_by_hash(_hash_reset_token(selection_token))
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired selection")

    now = int(time.time())
    if row.get("consumed"):
        raise HTTPException(
            status_code=400, detail="This selection has already been used"
        )
    if int(row.get("expires_at", 0)) < now:
        raise HTTPException(status_code=400, detail="This selection has expired")

    # Consume up-front so a double-submit can't double-send. Failures
    # below are per-account and fire-and-forget.
    await mark_reset_selection_consumed(str(row.get("_id")))

    chosen = set(account_refs or [])
    accounts = [a for a in (row.get("accounts") or []) if a.get("ref") in chosen]

    sent = 0
    for account in accounts:
        email = str(account.get("email") or "")
        if not email:
            continue
        token = secrets.token_urlsafe(32)
        try:
            await create_reset_token(
                token_hash=_hash_reset_token(token),
                user_id=str(account.get("user_id") or ""),
                user_type=str(account.get("user_type") or ""),
                tenant_id=account.get("tenant_id"),
                ttl_seconds=RESET_TOKEN_TTL_SECONDS,
                requesting_ip=requesting_ip,
            )
            await _send_reset_email(
                to_email=email,
                recipient_name=str(account.get("full_name") or ""),
                token=token,
                tenant_id=account.get("tenant_id"),
                requesting_ip=requesting_ip,
            )
            sent += 1
        except Exception:
            logger.warning(
                "send_reset_for_selection: issue failed for ref %s",
                account.get("ref"),
                exc_info=True,
            )

    return {"sent": sent}


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
        raise HTTPException(
            status_code=400, detail="This reset link has already been used"
        )
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
