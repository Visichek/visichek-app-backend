from __future__ import annotations

import logging

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from core.security_policy import get_security_policy
from schemas.imports import UserType
from security.hash import hash_password, check_password
from security.password_policy import (
    check_password_history,
    record_password_in_history,
    validate_password_strength,
)

logger = logging.getLogger(__name__)


async def _revoke_all_sessions_and_tokens(
    user_id: str, *, user_type: UserType
) -> None:
    """Terminate every active token + session for a user after a change.

    A self-service password change MUST invalidate all existing auth
    state — this device and every other — so the old password can never
    keep a session alive and a stolen session is cut off immediately.
    Without this, changing your password leaves previously-issued
    access/refresh tokens (and the rows behind the active-sessions list)
    fully usable until natural expiry.

    Mirrors the revocation already done by the authority-reset
    (``reset_system_user_password_by_authority``) and forgot-password
    (``reset_password_with_token``) flows. Best-effort: failures are
    logged but never block the change itself — token expiry and the
    gate-cache TTL are the backstop.
    """
    try:
        if user_type == UserType.ADMIN:
            from repositories.tokens_repo import delete_all_tokens_with_admin_id

            await delete_all_tokens_with_admin_id(adminId=user_id)
        else:
            from repositories.tokens_repo import delete_all_tokens_with_user_id

            await delete_all_tokens_with_user_id(userId=user_id)
    except Exception:
        logger.warning(
            "password change: token revocation failed for user_id=%s",
            user_id,
            exc_info=True,
        )

    try:
        from repositories.session_repo import delete_sessions

        await delete_sessions({"user_id": user_id, "user_type": user_type})
    except Exception:
        logger.warning(
            "password change: session revocation failed for user_id=%s",
            user_id,
            exc_info=True,
        )


async def _enforce_new_password_policy(
    user_id: str,
    new_password: str,
    role: str,
) -> bytes:
    """Validate and hash a new password against the platform security policy.

    Raises 422 with the policy's error list when validation fails, and 422
    when the password matches one of the user's last N hashes.
    """
    policy = await get_security_policy()
    result = validate_password_strength(new_password, policy=policy)
    if not result.is_valid:
        raise HTTPException(status_code=422, detail="; ".join(result.errors))

    safe = await check_password_history(
        user_id,
        new_password,
        role=role,
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

    return hash_password(new_password)


async def change_admin_password(
    admin_id: str,
    current_password: str,
    new_password: str,
) -> None:
    """Change an admin's password after verifying the current one."""
    admin = await db.admins.find_one({"_id": ObjectId(admin_id)})
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not check_password(current_password, admin["password"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    hashed = await _enforce_new_password_policy(admin_id, new_password, role="admin")

    # Also clear ``must_change_password`` — set by the admin invite
    # flow and any future authority-driven admin reset. Clearing it
    # here lifts the gate-side block in security/auth.py.
    await db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {"$set": {"password": hashed, "must_change_password": False}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        admin_id, hashed, role="admin", history_count=policy.password_history_count
    )

    # Kill every active token + session so the old password can no longer
    # ride a still-valid session and other devices are forced to re-login.
    await _revoke_all_sessions_and_tokens(admin_id, user_type=UserType.ADMIN)

    # Drop the cached gate snapshot so the next admin request re-reads
    # the row and sees ``must_change_password=false``.
    try:
        from core.queue.gate_cache import invalidate_gate

        invalidate_gate(user_id=admin_id)
    except Exception:
        pass


async def change_system_user_password(
    user_id: str,
    current_password: str,
    new_password: str,
) -> None:
    """Change a system user's password after verifying the current one."""
    user = await db.system_users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # System users store the password under ``password_hash`` (see
    # SystemUserCreate). The previous implementation read/wrote ``password``,
    # which silently bypassed verification on every call.
    if not check_password(current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    hashed = await _enforce_new_password_policy(
        user_id, new_password, role="system_user"
    )

    # Also clear the ``must_change_password`` flag. The flag is set by the
    # onboarding-accept + replace-super-admin flows when they auto-generate
    # a temporary password; clearing it here is what lifts the gate-side
    # block (security/account_status_check.py) so the user can resume
    # using the rest of the API.
    await db.system_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password_hash": hashed, "must_change_password": False}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        user_id,
        hashed,
        role="system_user",
        history_count=policy.password_history_count,
    )

    # Kill every active token + session so the old password can no longer
    # ride a still-valid session and other devices are forced to re-login.
    # This is what stops "I changed my password but the old one still
    # signs me in" for the account whose password just changed.
    await _revoke_all_sessions_and_tokens(user_id, user_type=UserType.SYSTEM_USER)

    # Drop the cached gate snapshot so the next request re-reads the row
    # and sees ``must_change_password=False``. Otherwise the user stays
    # locked out for up to the gate-cache TTL.
    try:
        from core.queue.gate_cache import invalidate_gate

        invalidate_gate(user_id=user_id)
    except Exception:
        pass


async def reset_system_user_password_by_authority(
    target_user_id: str,
    *,
    actor_id: str,
    actor_role: str,
    scope_tenant_id: str | None = None,
) -> None:
    """Reset another system user's password without knowing the old one.

    Used by:
      * Application admins (``actor_role="admin"``) — no tenant scope, can
        reset any system user's password including super_admins.
      * Tenant super_admins (``actor_role="super_admin"`` +
        ``scope_tenant_id``) — limited to users inside their own tenant.

    The new password is ALWAYS system-generated — the actor does not
    (and cannot) choose it. The cleartext value is emailed to the
    target user via the ``password_reset_temp`` template, never
    returned in the API response, and ``must_change_password`` is
    flipped to True so the target must pick their own on next login.

    Side effects: writes the new ``password_hash``, records it in
    ``password_history``, deletes every active token for the target user
    (so they're forced to log in again), invalidates the gate cache,
    emails the temp password, and emits a tenant-scoped audit event.
    """
    if not ObjectId.is_valid(target_user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    filter_doc: dict = {"_id": ObjectId(target_user_id)}
    if scope_tenant_id:
        filter_doc["tenant_id"] = scope_tenant_id

    user = await db.system_users.find_one(filter_doc)
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    # A super_admin cannot reset their own password through this path —
    # they should use the self-service /v1/auth/change-password flow which
    # requires the current password. Application admins can reset their own
    # via /v1/auth/change-password as well.
    if actor_id == target_user_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Use /v1/auth/change-password to change your own password. "
                "This endpoint is for resetting another user's password."
            ),
        )

    from security.password_policy import generate_secure_temp_password

    new_password = generate_secure_temp_password()
    hashed = await _enforce_new_password_policy(
        target_user_id, new_password, role="system_user"
    )

    # Authority resets always force a self-change on next login: the
    # actor (application admin or tenant super_admin) chose to trigger
    # the reset, so the target user must pick their own value before
    # they can hit the rest of the API. See the
    # ``must_change_password`` docstring on ``SystemUserBase`` for the
    # full lifecycle.
    await db.system_users.update_one(
        filter_doc,
        {"$set": {"password_hash": hashed, "must_change_password": True}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        target_user_id,
        hashed,
        role="system_user",
        history_count=policy.password_history_count,
    )

    # Force re-login: revoke every active token for the target so an
    # attacker holding an old session can't continue with stolen creds.
    try:
        from repositories.tokens_repo import delete_all_tokens_with_user_id

        await delete_all_tokens_with_user_id(userId=target_user_id)
    except Exception:
        pass

    # Drop the cached gate snapshot so the next request re-reads the row.
    try:
        from core.queue.gate_cache import invalidate_gate

        invalidate_gate(user_id=target_user_id)
    except Exception:
        pass

    # Email the temporary password — the only channel that ever sees
    # the cleartext. Fire-and-forget so an SMTP outage doesn't block
    # the reset itself; the actor can re-trigger if delivery fails.
    await _send_temp_password_reset_email(
        full_name=user.get("full_name") or "",
        email=user.get("email") or "",
        temp_password=new_password,
        actor_role=actor_role,
    )

    # Audit (mandatory for tenant-scoped writes).
    try:
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="system_user.password_reset",
            resource_type="system_user",
            resource_id=target_user_id,
            tenant_id=user.get("tenant_id"),
            details={
                "target_email": user.get("email"),
                "target_role": user.get("role"),
            },
        )
    except Exception:
        pass


async def _send_temp_password_reset_email(
    *,
    full_name: str,
    email: str,
    temp_password: str,
    actor_role: str,
) -> None:
    """Queue the authority-reset notification email.

    Fire-and-forget: failures only log so the reset itself never fails
    because of an SMTP hiccup. Uses the ``password_reset_temp``
    template (mounted alongside the rest of the auth templates).
    """
    if not email:
        return
    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest
        from core.settings import get_settings

        settings = get_settings()
        platform_name = settings.email_sender_name or "VisiChek"
        login_url = (settings.app_base_url or "").rstrip("/")
        if login_url:
            login_url = f"{login_url}/login"

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=email,
                template_key="password_reset_temp",
                context={
                    "recipient_name": full_name or "there",
                    "platform_name": platform_name,
                    "email": email,
                    "temp_password": temp_password,
                    "login_url": login_url,
                    "actor_role": actor_role,
                },
                dispatch="queued",
            )
        )
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "password_reset_temp email dispatch failed for %s",
            email,
            exc_info=True,
        )
