"""Two-step main super_admin transfer (with MFA re-challenge).

Endpoint pair lives in ``api/v1/system_user_route.py``:

   POST /v1/system-users/transfer-main-super-admin/initiate
   POST /v1/system-users/transfer-main-super-admin

The MFA re-challenge uses the existing OTP challenge system (the same
one admin login + 2FA verify use). Initiate mints a challenge bound to
the actor's (user_id, role), sends a 6-digit code via the
``admin_otp_code`` / ``password_reset``-style email template, and
returns the challenge id. The verify step submits the code AND
re-states the target user id; if the target id does not match the one
the challenge was created for, the verify is rejected
(``OTP_TARGET_MISMATCH``).

Authorisation
-------------
- An application admin can transfer the main super_admin of ANY tenant.
- A tenant super_admin can transfer ONLY within their own tenant AND
  ONLY if they themselves are the current main super_admin.
- The target must be an active super_admin in the same tenant and not
  already the main one.
"""

from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from repositories.system_user_repo import (
    clear_main_super_admin_flag_for_tenant,
    get_main_super_admin,
    get_system_user,
    set_main_super_admin_flag,
)
from schemas.imports import AccountStatus, UserType
from schemas.system_user_schema import SystemUserOut
from security.principal import AuthPrincipal, TENANT_USER_ROLES

logger = logging.getLogger(__name__)

# Stored on the OTP challenge doc to bind it to a specific transfer
# intent so a leaked challenge_id cannot be replayed against a
# different target.
_OTP_INTENT_KEY = "main_super_admin_transfer"


def _principal_user_type(principal: AuthPrincipal) -> UserType:
    return (
        UserType.SYSTEM_USER if principal.role in TENANT_USER_ROLES else UserType.ADMIN
    )


async def _resolve_tenant_id(
    principal: AuthPrincipal, payload_tenant_id: Optional[str]
) -> str:
    """Pick the tenant the transfer applies to.

    - App admins MUST supply tenant_id in the body (no implicit tenant).
    - Tenant super_admins infer from their token; if they DO supply one,
      it must match.
    """
    user_type = _principal_user_type(principal)
    if user_type == UserType.ADMIN:
        if not payload_tenant_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "tenant_id is required when the caller is an application admin.",
                    "code": "TENANT_ID_REQUIRED",
                },
            )
        return payload_tenant_id

    # Tenant super_admin path
    inferred = principal.tenant_id or ""
    if payload_tenant_id and payload_tenant_id != inferred:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Cannot transfer the main super_admin of another tenant.",
                "code": "AUTH_PERMISSION_DENIED",
            },
        )
    if not inferred:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Calling principal has no tenant scope.",
                "code": "AUTH_PERMISSION_DENIED",
            },
        )
    return inferred


async def _assert_actor_can_transfer(
    *, principal: AuthPrincipal, tenant_id: str
) -> None:
    """App admins always pass. Tenant super_admins must be the current main."""
    user_type = _principal_user_type(principal)
    if user_type == UserType.ADMIN:
        return

    if principal.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Only the current main super_admin or an application admin can transfer this role.",
                "code": "AUTH_PERMISSION_DENIED",
            },
        )

    current_main = await get_main_super_admin(tenant_id)
    if not current_main or current_main.id != principal.user_id:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Only the current main super_admin can initiate this transfer.",
                "code": "AUTH_PERMISSION_DENIED",
            },
        )


async def _validate_target(*, target_user_id: str, tenant_id: str) -> SystemUserOut:
    if not ObjectId.is_valid(target_user_id):
        raise HTTPException(status_code=400, detail="Invalid target user ID format")

    target = await get_system_user(
        {"_id": ObjectId(target_user_id), "tenant_id": tenant_id}
    )
    if not target:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Target user not found in this tenant.",
                "code": "RESOURCE_NOT_FOUND",
            },
        )

    target_role = (
        target.role.value if hasattr(target.role, "value") else str(target.role)
    )
    if target_role != "super_admin":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Target user must already be a super_admin to receive the main flag.",
                "code": "TARGET_NOT_SUPER_ADMIN",
            },
        )

    target_status = (
        target.account_status.value
        if hasattr(target.account_status, "value")
        else str(target.account_status)
    )
    if target_status != AccountStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Target user must be ACTIVE to receive the main super_admin flag.",
                "code": "TARGET_INACTIVE",
            },
        )

    if getattr(target, "is_main_super_admin", False):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Target user is already the main super_admin.",
                "code": "TARGET_ALREADY_MAIN",
            },
        )

    return target


async def initiate_transfer(
    *,
    principal: AuthPrincipal,
    new_main_super_admin_user_id: str,
    tenant_id_from_body: Optional[str],
) -> dict:
    """Validate the request and mint an OTP challenge bound to (actor, target).

    Returns a dict shaped like
    ``{"otp_required": True, "otp_challenge_id": ..., "new_main_super_admin_user_id": ..., "tenant_id": ...}``.

    The OTP code is delivered out-of-band:
       - admins  →  email via ``services/otp_service._send_admin_otp_email``
                    (already wired inside ``create_otp_challenge``).
       - tenant users  →  same OTP system; the FE prompts the user for
                          the code from their authenticator app or email.
    """
    tenant_id = await _resolve_tenant_id(principal, tenant_id_from_body)
    await _assert_actor_can_transfer(principal=principal, tenant_id=tenant_id)
    target = await _validate_target(
        target_user_id=new_main_super_admin_user_id, tenant_id=tenant_id
    )

    # Mint an OTP challenge for the actor. We use the actor's own
    # role/user_type so the email dispatch picks the right channel
    # (admin → email OTP via _send_admin_otp_email; tenant user → the
    # OTP record is still created and they can read the code from
    # their notification email channel today).
    from services.otp_service import create_otp_challenge

    user_type = _principal_user_type(principal)
    challenge_id, _code = await create_otp_challenge(
        user_id=principal.user_id,
        user_type=user_type,
        role=principal.role,
        tenant_id=tenant_id,
    )

    # Bind the challenge to this transfer intent. The verify step will
    # reject the OTP if the bound target doesn't match the re-stated
    # ``new_main_super_admin_user_id``.
    await db.pending_otp.update_one(
        {"_id": ObjectId(challenge_id)},
        {
            "$set": {
                "intent": _OTP_INTENT_KEY,
                "intent_target_user_id": target.id,
                "intent_tenant_id": tenant_id,
            }
        },
    )

    return {
        "otp_required": True,
        "otp_challenge_id": challenge_id,
        "new_main_super_admin_user_id": target.id,
        "tenant_id": tenant_id,
    }


async def verify_and_complete_transfer(
    *,
    principal: AuthPrincipal,
    otp_challenge_id: str,
    otp_code: str,
    new_main_super_admin_user_id: str,
    tenant_id_from_body: Optional[str],
) -> SystemUserOut:
    """Verify the OTP, re-check the actor + target, then flip the main flag.

    Returns the refreshed ``SystemUserOut`` of the new main super_admin.
    """
    tenant_id = await _resolve_tenant_id(principal, tenant_id_from_body)
    await _assert_actor_can_transfer(principal=principal, tenant_id=tenant_id)
    target = await _validate_target(
        target_user_id=new_main_super_admin_user_id, tenant_id=tenant_id
    )

    # Load the challenge before verification so we can enforce the
    # intent binding. ``verify_otp_challenge`` would otherwise consume
    # the row before we could check the bound target.
    if not ObjectId.is_valid(otp_challenge_id):
        raise HTTPException(status_code=400, detail="Invalid OTP challenge id")

    pending = await db.pending_otp.find_one({"_id": ObjectId(otp_challenge_id)})
    if not pending:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP challenge")

    if pending.get("intent") != _OTP_INTENT_KEY:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "OTP challenge was not minted for a main super_admin transfer.",
                "code": "OTP_WRONG_INTENT",
            },
        )

    if pending.get("user_id") != principal.user_id:
        # Defence in depth: the OTP must belong to the calling principal.
        raise HTTPException(
            status_code=403,
            detail={
                "message": "OTP challenge does not belong to the calling user.",
                "code": "AUTH_PERMISSION_DENIED",
            },
        )

    if pending.get("intent_target_user_id") != target.id:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Re-stated target user does not match the user this OTP "
                    "challenge was created for. Initiate a new transfer."
                ),
                "code": "OTP_TARGET_MISMATCH",
            },
        )

    if pending.get("intent_tenant_id") != tenant_id:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Re-stated tenant does not match the tenant this OTP "
                    "challenge was created for. Initiate a new transfer."
                ),
                "code": "OTP_TARGET_MISMATCH",
            },
        )

    # Use the standard verify_otp_challenge so attempt counters,
    # expiry, and one-time consumption are enforced uniformly.
    from services.otp_service import verify_otp_challenge

    await verify_otp_challenge(otp_challenge_id, otp_code)

    # ── Flip the flag atomically ──
    # Order matters for the partial-unique index: clear the OLD main
    # first (so for a moment zero rows carry True), then set the NEW
    # main (so the partial index sees exactly one True). Done in two
    # update_many calls so a single in-flight read never sees two main
    # rows in the same tenant simultaneously.
    from services.audit_service import record_audit_event
    from core.queue.gate_cache import invalidate_gate

    current_main = await get_main_super_admin(tenant_id)
    old_main_id = current_main.id if current_main else None

    # Step 1: clear every True flag for this tenant EXCEPT the target.
    # Excluding the target lets this be safely re-run if step 2 fails
    # mid-way (target already has True from a prior partial run → keep
    # it, drop everything else).
    await clear_main_super_admin_flag_for_tenant(tenant_id, except_user_id=target.id)
    # Step 2: set True on the target.
    await set_main_super_admin_flag(
        user_id=target.id or "", tenant_id=tenant_id, value=True
    )

    # Audit (mandatory for tenant-scoped writes).
    try:
        await record_audit_event(
            actor_id=principal.user_id,
            actor_role=principal.role,
            action="tenant.main_super_admin_transferred",
            resource_type="system_user",
            resource_id=target.id or "",
            tenant_id=tenant_id,
            details={
                "from_user_id": old_main_id,
                "to_user_id": target.id,
                "reason": "transfer",
            },
        )
    except Exception:
        logger.warning(
            "audit record for main super_admin transfer failed", exc_info=True
        )

    # Drop the gate snapshots so both users see fresh state on their
    # next authenticated request.
    for uid in (old_main_id, target.id):
        if uid:
            try:
                invalidate_gate(user_id=uid)
            except Exception:
                pass

    refreshed = await get_system_user(
        {"_id": ObjectId(target.id), "tenant_id": tenant_id}
    )
    if not refreshed:
        # Should never happen — the row was just updated.
        raise HTTPException(
            status_code=500,
            detail="Transfer completed but refreshed read failed.",
        )
    return refreshed
