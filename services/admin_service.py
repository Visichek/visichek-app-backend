import logging
import os

from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.admin_repo import (
    create_admin,
    get_admin,
    get_admins,
    search_admins,
    update_admin,
    delete_admin,
)
from schemas.admin_schema import (
    AdminCreate,
    AdminUpdate,
    AdminOut,
    AdminLogin,
    AdminRefresh,
    AdminSearchResult,
    AdminSignupRequest,
)
from security.hash import check_password
from repositories.tokens_repo import (
    get_refresh_tokens,
    delete_access_token,
    delete_refresh_token,
    delete_all_tokens_with_admin_id,
)
from services.auth_helpers import issue_tokens_for_user
from core.email_utils import normalize_email
from config.role_permissions import (
    get_default_permissions_for_admin_preset,
    get_default_permissions_for_role,
)
from schemas.imports import AccountStatus, PermissionList, UserType


logger = logging.getLogger(__name__)

PRIMARY_ADMIN_ID = "656f7ac12b9d4f6c9e2b9f7d"

ACCESS_PRESET_LABELS = {
    "content_only": "content editor",
    "support_only": "support operator",
    "content_support": "content + support operator",
    "billing_only": "billing operator",
    "all_controls": "platform admin",
}


def _is_primary_env_admin_id(admin_id: str | None, email: str | None = None) -> bool:
    """True for the env-pinned primary platform admin.

    Guards delete / disable paths so the bootstrap account can never be
    locked out. Matches by id first (the hardcoded sentinel returned by
    repositories.admin_repo when no DB row exists) and falls back to a
    case-insensitive email compare against ``SUPER_ADMIN_EMAIL``.
    """
    if admin_id and admin_id == PRIMARY_ADMIN_ID:
        return True
    primary_email = (os.getenv("SUPER_ADMIN_EMAIL") or "").strip().lower()
    if not primary_email:
        return False
    if not email:
        return False
    return email.strip().lower() == primary_email


async def _send_admin_invite_email(
    *,
    full_name: str,
    email: str,
    temp_password: str,
    access_preset: str | None,
    inviter_name: str,
) -> None:
    """Mail the welcome / first-login instructions to an invited admin.

    Fire-and-forget: failures only log. The admin row exists either way
    — the inviter can resend the temp password from the admin console
    if the email never lands.
    """
    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest
        from core.settings import get_settings

        settings = get_settings()
        platform_name = settings.email_sender_name or "VisiChek"
        access_label = ACCESS_PRESET_LABELS.get(
            (access_preset or "all_controls").lower(), "platform admin"
        )
        login_url = (settings.app_base_url or "").rstrip("/")
        if login_url:
            # Admins sign in at the platform console — /login is a retired
            # chooser that now redirects to the tenant portal.
            login_url = f"{login_url}/admin/login"

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=email,
                template_key="admin_invite",
                context={
                    "invitee_name": full_name or "there",
                    "platform_name": platform_name,
                    "inviter_name": inviter_name or "An administrator",
                    "email": email,
                    "temp_password": temp_password,
                    "access_label": access_label,
                    "login_url": login_url,
                },
                dispatch="auto",
            )
        )
    except Exception:
        logger.warning("admin invite email send failed for %s", email, exc_info=True)


async def add_admin(signup_data: AdminSignupRequest, invited_by: str) -> AdminOut:
    """Create a new admin from an invite request.

    - Normalizes email to prevent +alias / dot duplicates
    - Auto-assigns default admin permissions
    - Sets account_status to ACTIVE
    - Generates a temporary password (the inviter does not choose it),
      flips ``must_change_password=True`` on the new row, and emails
      the cleartext value via the ``admin_invite`` template
    """
    from core.test_mode import issue_temp_password
    from security.temp_password import issued_at_now as _issued_at_now

    normalized = normalize_email(signup_data.email)

    # Check for existing admin using normalized email
    existing = await get_admin(filter_dict={"email": signup_data.email})
    if existing:
        raise HTTPException(status_code=409, detail="Admin already exists")

    # Also check by normalized email (catches alias tricks)
    all_admins = await get_admins(start=0, stop=10000)
    for admin in all_admins:
        if normalize_email(admin.email) == normalized:
            raise HTTPException(
                status_code=409, detail="An admin with this email already exists"
            )

    # Issue 10 backend: resolve permissions from the requested access
    # preset rather than the blanket "all admin" set. Falls back to
    # the legacy role-based default when no preset was supplied so
    # CLI / tooling that hasn't adopted the field keeps producing
    # all_controls admins. The selected preset is persisted onto the
    # admin record so the frontend ``AdminProfile.accessPreset`` field
    # is populated for the nav filter.
    if signup_data.access_preset:
        permission_list = get_default_permissions_for_admin_preset(
            signup_data.access_preset
        )
    else:
        permission_list = get_default_permissions_for_role("admin")

    # The inviter doesn't (and cannot) choose the password — generate
    # a policy-compliant value here and email it. The cleartext is
    # never returned in the API response.
    temp_password = issue_temp_password(signup_data.email)

    admin_data = AdminCreate(
        full_name=signup_data.full_name,
        email=signup_data.email,
        password=temp_password,
        accountStatus=AccountStatus("ACTIVE"),
        permissionList=permission_list,
        access_preset=signup_data.access_preset,
        # Every invited admin is required to complete 2FA on each login.
        # The login flow honours mfa_enabled via is_mfa_required() and
        # admins also pick up enforce_totp_for_admins from platform policy
        # — setting both belt-and-braces so a future policy flip cannot
        # silently weaken the security posture for invited admins.
        mfa_enabled=True,
        must_change_password=True,
        must_change_password_at=_issued_at_now(),
        invited_by=invited_by,
    )

    new_admin = await create_admin(admin_data)

    # Resolve the inviter's display name for the welcome email — best
    # effort, falls through to "An administrator" if the inviter row
    # has been deleted or invited_by is the env primary admin (no DB row).
    inviter_name = ""
    try:
        if ObjectId.is_valid(invited_by):
            inviter_doc = await get_admin({"_id": ObjectId(invited_by)})
            if inviter_doc:
                inviter_name = inviter_doc.full_name or ""
    except Exception:
        inviter_name = ""

    await _send_admin_invite_email(
        full_name=signup_data.full_name,
        email=signup_data.email,
        temp_password=temp_password,
        access_preset=signup_data.access_preset,
        inviter_name=inviter_name,
    )

    access_token, refresh_token = await issue_tokens_for_user(
        user_id=new_admin.id or "", role="admin"
    )  # type: ignore
    new_admin.password = ""
    new_admin.access_token = access_token
    new_admin.refresh_token = refresh_token
    return new_admin


async def authenticate_admin(admin_data: AdminLogin) -> AdminOut:
    from core.security_policy import get_security_policy
    from security.password_policy import (
        check_login_lockout,
        record_failed_login,
        clear_failed_logins,
    )

    policy = await get_security_policy()

    # Check lockout before anything else
    lockout = await check_login_lockout(admin_data.email)
    if lockout:
        minutes = lockout["remaining_seconds"] // 60
        raise HTTPException(
            status_code=429,
            detail=f"Account temporarily locked due to too many failed login attempts. Try again in {minutes} minute(s).",
        )

    admin = await get_admin(filter_dict={"email": admin_data.email})

    if admin is not None:
        if check_password(password=admin_data.password, hashed=admin.password):  # type: ignore
            from security.temp_password import (
                is_temp_password_expired,
                raise_temp_password_expired,
            )

            # The credential is correct, but an admin-issued temporary password
            # is only good for a bounded window. Past it, the emailed cleartext
            # stops being a key — an administrator must issue a fresh one.
            if is_temp_password_expired(admin):
                raise_temp_password_expired()

            await clear_failed_logins(admin_data.email)
            admin.password = ""

            # 2FA check — platform policy decides whether admins need OTP.
            from services.otp_service import is_mfa_required, create_otp_challenge

            if await is_mfa_required(UserType.ADMIN, admin.id):  # type: ignore
                challenge_id, _code = await create_otp_challenge(
                    user_id=admin.id or "",
                    user_type=UserType.ADMIN,
                    role="admin",  # type: ignore
                )
                return {"otp_required": True, "otp_challenge_id": challenge_id}  # type: ignore[return-value]

            access_token, refresh_token = await issue_tokens_for_user(
                user_id=admin.id or "", role="admin"
            )  # type: ignore
            admin.access_token = access_token
            admin.refresh_token = refresh_token
            return admin
        else:
            lockout_status = await record_failed_login(admin_data.email, policy=policy)
            if lockout_status.get("locked"):
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Too many failed login attempts. Account is temporarily "
                        f"locked for {policy.lockout_duration_minutes} minute(s)."
                    ),
                )
            remaining = lockout_status.get("attempts_remaining", "?")
            raise HTTPException(
                status_code=401,
                detail=f"Invalid login credentials. {remaining} attempt(s) remaining before lockout.",
            )
    else:
        raise HTTPException(status_code=401, detail="Invalid login credentials")


async def verify_admin_otp(challenge_id: str, otp_code: str) -> AdminOut:
    """Step 2 of admin 2FA login — verify OTP and issue tokens."""
    from services.otp_service import verify_otp_challenge

    result = await verify_otp_challenge(challenge_id, otp_code)
    admin = await get_admin(filter_dict={"_id": ObjectId(result["user_id"])})
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")

    admin.password = ""
    access_token, refresh_token = await issue_tokens_for_user(
        user_id=admin.id or "", role="admin"
    )  # type: ignore
    admin.access_token = access_token
    admin.refresh_token = refresh_token
    return admin


async def refresh_admin_tokens_reduce_number_of_logins(
    admin_refresh_data: AdminRefresh, expired_access_token
):
    refreshObj = await get_refresh_tokens(admin_refresh_data.refresh_token)
    if refreshObj:
        if refreshObj.previousAccessToken == expired_access_token:
            admin = await get_admin(filter_dict={"_id": ObjectId(refreshObj.userId)})

            if admin is not None:
                access_token, refresh_token = await issue_tokens_for_user(
                    user_id=admin.id or "", role="admin"
                )  # type: ignore
                admin.access_token = access_token
                admin.refresh_token = refresh_token
                await delete_access_token(accessToken=expired_access_token)
                await delete_refresh_token(
                    refreshToken=admin_refresh_data.refresh_token
                )
                return admin

    raise HTTPException(status_code=404, detail="Invalid refresh token")


async def remove_admin(admin_id: str):
    # Hard guard: the env-pinned primary admin (id sentinel
    # ``656f7ac12b9d4f6c9e2b9f7d`` or matching SUPER_ADMIN_EMAIL) cannot
    # be deleted under any circumstance — losing it would lock the
    # platform out of its own bootstrap account.
    if _is_primary_env_admin_id(admin_id):
        raise HTTPException(
            status_code=403,
            detail="The primary platform admin cannot be deleted.",
        )

    # The sentinel id is not a real Mongo ObjectId in some legacy callers;
    # the env primary admin row may not exist at all. Guard both cases.
    if not ObjectId.is_valid(admin_id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    existing = await get_admin({"_id": ObjectId(admin_id)})
    if existing and _is_primary_env_admin_id(existing.id, existing.email):
        raise HTTPException(
            status_code=403,
            detail="The primary platform admin cannot be deleted.",
        )

    filter_dict = {"_id": ObjectId(admin_id)}
    result = await delete_admin(filter_dict)
    await delete_all_tokens_with_admin_id(adminId=admin_id)

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Admin not found")


async def retrieve_admin_by_admin_id(id: str) -> AdminOut:
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    filter_dict = {"_id": ObjectId(id)}
    result = await get_admin(filter_dict)

    if not result:
        raise HTTPException(status_code=404, detail="Admin not found")

    return result


async def retrieve_admins(start=0, stop=100) -> List[AdminOut]:
    return await get_admins(start=start, stop=stop)


async def search_admins_by_query(
    query: str, start: int = 0, stop: int = 50
) -> List[AdminSearchResult]:
    """Search admins by id, email, or full name and return trimmed records."""
    if not query or not query.strip():
        raise HTTPException(
            status_code=400, detail="Search query 'q' must not be empty"
        )
    matches = await search_admins(query=query, start=start, stop=stop)
    return [AdminSearchResult.from_admin_out(admin) for admin in matches]


async def update_admin_access_preset(
    admin_id: str,
    new_preset: str,
) -> AdminOut:
    """Re-scope an existing admin's access preset.

    Re-derives the matching permission slice from
    ``config.role_permissions.ADMIN_ACCESS_PRESETS`` so the next request
    the admin makes already enforces the new scope (Phase A of Issue 10
    backend uses the live permissionList for route gating). The env
    primary admin is locked at ``all_controls`` and cannot be downgraded.
    """
    if not ObjectId.is_valid(admin_id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    existing = await get_admin({"_id": ObjectId(admin_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Admin not found")

    if (
        _is_primary_env_admin_id(existing.id, existing.email)
        and new_preset != "all_controls"
    ):
        raise HTTPException(
            status_code=403,
            detail="The primary platform admin must remain on the all_controls preset.",
        )

    permission_list = get_default_permissions_for_admin_preset(new_preset)

    await update_admin(
        {"_id": ObjectId(admin_id)},
        AdminUpdate(),  # last_updated bump only; we $set the two fields below
    )
    # AdminUpdate doesn't expose access_preset / permissionList today (it
    # only exists for the password-change path). Patch directly so we
    # don't bloat the public update schema with admin-internal fields.
    from core.database import db as _db

    await _db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {
            "$set": {
                "access_preset": new_preset,
                "permissionList": permission_list.model_dump(),
            }
        },
    )

    refreshed = await get_admin({"_id": ObjectId(admin_id)})
    if not refreshed:
        raise HTTPException(status_code=404, detail="Admin not found after update")
    refreshed.password = None  # type: ignore[assignment]
    return refreshed


async def heal_admin_permissions_to_preset(
    admin: AdminOut,
) -> PermissionList:
    """Re-derive an admin's stored ``permissionList`` from their preset.

    Called from the admin gate check whenever the keys we have stored
    diverge from what the admin's ``access_preset`` should currently grant
    — typically because a new admin route shipped and ``ADMIN_PERMISSIONS``
    was backfilled, so the preset now resolves to a superset of what was
    persisted at invite time.

    Persists directly (no queued writer) because the gate check is on
    every authenticated admin request and we want the very call that
    triggered the heal to succeed against the fresh keys. Also drops the
    cached gate snapshot so the next request reads the updated
    ``permissionList`` from the rebuilt cache, not a stale one.

    Returns the recomputed ``PermissionList`` so the caller can use it
    for the in-flight permission check without re-reading the admin row.
    """
    from core.database import db as _db
    from core.queue.gate_cache import invalidate_gate

    preset = getattr(admin, "access_preset", None) or "all_controls"
    expected = get_default_permissions_for_admin_preset(preset)

    if not admin.id or not ObjectId.is_valid(admin.id):
        # Should not happen for an authenticated admin — fall through with
        # the recomputed list so this request still works.
        logger.warning(
            "heal_admin_permissions_to_preset called with invalid admin id %r",
            admin.id,
        )
        return expected

    try:
        await _db.admins.update_one(
            {"_id": ObjectId(admin.id)},
            {"$set": {"permissionList": expected.model_dump()}},
        )
        invalidate_gate(admin.id, role="admin")
        logger.info(
            "Re-derived permissionList for admin %s from preset=%s "
            "(stored list drifted from expected keys).",
            admin.id,
            preset,
        )
    except Exception:
        # Persistence failure is non-fatal — the request still goes
        # through against ``expected``; the next gate check will retry.
        logger.warning(
            "Failed to persist healed permissionList for admin %s; "
            "request continues against in-memory list",
            admin.id,
            exc_info=True,
        )
    return expected


async def update_admin_by_id(
    admin_id: str,
    admin_data: AdminUpdate,
    is_password_getting_changed: bool = False,
) -> AdminOut:
    from core.queue.manager import QueueManager

    if not ObjectId.is_valid(admin_id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    filter_dict = {"_id": ObjectId(admin_id)}
    result = await update_admin(filter_dict, admin_data)

    if not result:
        raise HTTPException(status_code=404, detail="Admin not found or update failed")

    if is_password_getting_changed:
        if admin_data.password:
            from security.password_policy import record_password_in_history

            pwd_hash = admin_data.password
            if isinstance(pwd_hash, bytes):
                pwd_hash = pwd_hash.decode("utf-8")
            await record_password_in_history(admin_id, pwd_hash, role="admin")

        QueueManager.get_instance().enqueue("delete_tokens", {"userId": admin_id})

    return result
