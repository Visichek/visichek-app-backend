from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from core.security_policy import get_security_policy
from security.hash import hash_password, check_password
from security.password_policy import (
    check_password_history,
    record_password_in_history,
    validate_password_strength,
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

    await db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {"$set": {"password": hashed}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        admin_id, hashed, role="admin", history_count=policy.password_history_count
    )


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

    await db.system_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password_hash": hashed}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        user_id,
        hashed,
        role="system_user",
        history_count=policy.password_history_count,
    )


async def reset_system_user_password_by_authority(
    target_user_id: str,
    new_password: str,
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

    Side effects: writes the new ``password_hash``, records it in
    ``password_history``, deletes every active token for the target user
    (so they're forced to log in again), and emits a tenant-scoped audit
    event.
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

    hashed = await _enforce_new_password_policy(
        target_user_id, new_password, role="system_user"
    )

    await db.system_users.update_one(
        filter_doc,
        {"$set": {"password_hash": hashed}},
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
