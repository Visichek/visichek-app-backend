import asyncio
import time

from bson import ObjectId
from fastapi import HTTPException
from typing import Any, List

from repositories.system_user_repo import (
    count_system_users,
    create_system_user,
    get_raw_system_users_by_email,
    get_system_user,
    get_system_users,
    update_system_user,
    delete_system_user,
)
from repositories.tokens_repo import (
    get_refresh_tokens,
    delete_access_token,
    delete_refresh_token,
    delete_all_tokens_with_user_id,
)
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
    SystemUserRefresh,
    SystemUserSignupRequest,
    SystemUserTenantLogin,
)
from schemas.imports import AccountStatus
from security.hash import check_password
from services.auth_helpers import issue_tokens_for_role
from core.email_utils import normalize_email
from config.role_permissions import get_default_permissions_for_role
from services.audit_service import record_audit_event
from services.plan_limits import enforce_entity_cap


async def _resolve_branch_ids_for_user_assignment(
    tenant_id: str,
    requested: list[str] | None,
) -> list[str]:
    """Return the canonical branch_ids to persist on a system_user row.

    Rules:
      * If ``requested`` is None or empty → defaults to ``[headquarters_id]``,
        falling back to the first active branch when no headquarters exists.
        If the tenant has no branches at all, one is provisioned via
        ``ensure_default_branch`` so every user always lands on at least one.
      * Every requested id must belong to the tenant.
      * The total count must respect the plan's ``max_branches`` cap. Single
        branch is always allowed (no cap); multi-branch requires the plan
        feature.
      * Result is always non-empty and de-duplicated.
    """
    from repositories.branch_repo import get_branches
    from services.branch_service import ensure_default_branch
    from services.tenant_service import retrieve_tenant_by_id

    tenant_branches = await get_branches({"tenant_id": tenant_id}, start=0, stop=1000)
    if not tenant_branches:
        # Tenants are supposed to be bootstrapped with a HQ branch but
        # historical tenants may not be. Provision lazily so user invites
        # never fail with "no branches exist for this tenant".
        try:
            tenant = await retrieve_tenant_by_id(tenant_id)
            company_name = tenant.company_name or "HQ"
        except Exception:
            company_name = "HQ"
        hq = await ensure_default_branch(tenant_id, company_name)
        tenant_branches = [hq]

    valid_ids = {b.id for b in tenant_branches if b.id}

    if not requested:
        # Pick HQ if present, else the first branch.
        hq_branch = next(
            (b for b in tenant_branches if b.is_headquarters and b.id),
            None,
        )
        chosen = hq_branch.id if hq_branch and hq_branch.id else tenant_branches[0].id
        return [chosen or ""]

    # De-duplicate while preserving order.
    seen: set[str] = set()
    cleaned: list[str] = []
    for bid in requested:
        if bid and bid not in seen:
            seen.add(bid)
            cleaned.append(bid)

    unknown = [bid for bid in cleaned if bid not in valid_ids]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "One or more branch_ids do not belong to this tenant",
                "unknown_branch_ids": unknown,
            },
        )

    if len(cleaned) > 1:
        # Multi-branch assignment requires plan support (max_branches > 1).
        try:
            from services.plan_cache_service import resolve_tenant_plan

            plan_data = await resolve_tenant_plan(tenant_id)
        except Exception:
            plan_data = None

        max_branches = None
        if plan_data:
            max_branches = (plan_data.get("tenant_caps") or {}).get("max_branches")

        if max_branches is not None and max_branches < len(cleaned):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your plan allows at most {max_branches} branch(es) per user. "
                    "Upgrade your plan to assign a user to multiple branches."
                ),
            )

    return cleaned


async def _check_email_uniqueness(email: str, role: str, tenant_id: str) -> None:
    """Enforce per-tenant email uniqueness for system users.

    The same address may legitimately exist across different tenants — at login
    the tenant-selection challenge (``authenticate_system_user``) lets the user
    pick which tenant to sign in to. This applies uniformly to every role,
    including ``super_admin``.
    """
    del role  # uniqueness scope is the tenant, not the role
    normalized = normalize_email(email)

    tenant_users = await get_system_users(
        filter_dict={"tenant_id": tenant_id}, start=0, stop=50000
    )
    for user in tenant_users:
        if normalize_email(user.email) == normalized:
            raise HTTPException(
                status_code=409,
                detail="A user with this email already exists in this tenant",
            )


async def add_system_user(
    user_data: SystemUserCreate,
    *,
    preassigned_id: str | None = None,
) -> SystemUserOut:
    """Create a system user with auto-assigned permissions based on role."""

    # Enforce plan cap on total system users for this tenant
    current_count = await count_system_users({"tenant_id": user_data.tenant_id})
    await enforce_entity_cap(
        tenant_id=user_data.tenant_id,
        cap_key="max_system_users",
        current_count=current_count,
        friendly_name="System user",
    )

    # Enforce email uniqueness with normalization
    await _check_email_uniqueness(
        email=user_data.email,
        role=user_data.role.value
        if hasattr(user_data.role, "value")
        else user_data.role,
        tenant_id=user_data.tenant_id,
    )

    # Auto-assign permissions based on role
    role_str = (
        user_data.role.value if hasattr(user_data.role, "value") else user_data.role
    )
    user_data.permissionList = get_default_permissions_for_role(role_str)

    # Resolve and validate branch_ids — every user lands on at least one branch.
    user_data.branch_ids = await _resolve_branch_ids_for_user_assignment(
        tenant_id=user_data.tenant_id,
        requested=list(user_data.branch_ids) if user_data.branch_ids else None,
    )

    new_user = await create_system_user(user_data, preassigned_id=preassigned_id)
    access_token, refresh_token = await issue_tokens_for_role(
        user_id=new_user.id or "",
        role=new_user.role.value,
        tenant_id=new_user.tenant_id,
        branch_ids=list(getattr(new_user, "branch_ids", None) or []),
    )
    new_user.access_token = access_token
    new_user.refresh_token = refresh_token

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="system_user.created",
            resource_type="system_user",
            resource_id=str(new_user.id),
            tenant_id=new_user.tenant_id,
            details={
                "email": new_user.email,
                "role": new_user.role.value
                if hasattr(new_user.role, "value")
                else new_user.role,
                "department_id": new_user.department_id,
                "branch_ids": list(new_user.branch_ids or []),
                "full_name": new_user.full_name,
            },
        )
    except Exception:
        pass

    return new_user


async def add_super_admin_to_tenant(
    tenant_id: str,
    full_name: str,
    email: str,
    password: str,
    branch_ids: list[str] | None = None,
) -> SystemUserOut:
    """Create a super_admin for an *existing* tenant.

    Distinct from ``services.tenant_service.bootstrap_tenant`` (which creates
    the tenant + first super_admin atomically). Use this to recover a
    tenant whose super_admin was offboarded (zero active super_admins
    remaining).

    Singleton invariant: a tenant may have at most ONE active super_admin
    at a time. If the tenant already has an active super_admin this call
    is rejected with 409 ``SUPER_ADMIN_ALREADY_EXISTS``. To replace the
    existing super_admin, use the MFA-protected transfer flow
    (``POST /v1/system-users/transfer-main-super-admin/initiate``) or
    offboard the tenant first. This is enforced regardless of plan tier
    — the per-plan ``max_system_users`` cap is an additional, separate
    check that ``add_system_user`` runs further down the stack.

    Reuses the standard ``add_system_user`` so plan caps, email
    uniqueness, permission defaults, branch validation and audit logging
    all run. The new row is always marked ``is_main_super_admin=True``
    because at this point we have just verified zero existing
    super_admins.
    """
    from schemas.imports import AccountStatus as _AccountStatus
    from schemas.imports import SystemUserRole as _SystemUserRole
    from services.tenant_service import retrieve_tenant_by_id
    from repositories.system_user_repo import count_active_super_admins

    # Validate the tenant exists and is active before we even hash the
    # password — avoids creating an orphaned user on a deleted tenant.
    tenant = await retrieve_tenant_by_id(tenant_id)
    if not getattr(tenant, "is_active", True):
        raise HTTPException(
            status_code=400,
            detail="Cannot add a super admin to an inactive tenant",
        )

    existing_super_count = await count_active_super_admins(tenant_id)
    if existing_super_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "This tenant already has an active super admin. A tenant "
                    "may have at most one super admin at a time. Use "
                    "POST /v1/system-users/transfer-main-super-admin/initiate "
                    "to move ownership to a different user, or offboard the "
                    "tenant first via "
                    "POST /v1/admins/tenants/{tenant_id}/offboard."
                ),
                "code": "SUPER_ADMIN_ALREADY_EXISTS",
                "tenant_id": tenant_id,
                "active_super_admins": existing_super_count,
            },
        )

    create_data = SystemUserCreate(
        tenant_id=tenant_id,
        branch_ids=list(branch_ids) if branch_ids else [],
        full_name=full_name,
        email=email,
        role=_SystemUserRole.SUPER_ADMIN,
        account_status=_AccountStatus.ACTIVE,
        is_active=True,
        password_hash=password,
        is_main_super_admin=True,
    )
    return await add_system_user(create_data)


async def replace_super_admin_for_tenant(
    tenant_id: str,
    full_name: str,
    email: str,
    *,
    password: str | None = None,
    branch_ids: list[str] | None = None,
    actor_id: str = "system",
) -> dict:
    """Atomically swap the tenant's lone super_admin for a new one.

    Use this when the existing super_admin is unreachable or has to be
    handed over to a different person. The flow is:

      1. Clear ``is_main_super_admin`` on the existing main super_admin
         (the partial-unique index forbids two ``True`` rows in the same
         tenant — so we must drop the flag before we set it elsewhere).
      2. Mark the old super_admin INACTIVE + ``is_active=False`` and
         revoke their tokens. Bypasses ``guard_system_user_update``
         because replacement is an authority-driven operation (same
         escape hatch ``tenant_offboarding_service`` uses for the
         deactivation sweep).
      3. Create the new super_admin with ``is_main_super_admin=True``.
      4. Email the welcome / temporary password to the new super_admin.

    ``password`` is optional — when omitted a policy-compliant temporary
    password is generated and surfaced via the welcome email; the raw
    value is never returned in the API response.

    Distinct from ``add_super_admin_to_tenant`` (which refuses when one
    already exists) and from ``transfer-main-super-admin`` (which moves
    the main flag between two *existing* super_admins).
    """
    from bson import ObjectId as _ObjectId

    from repositories.system_user_repo import (
        clear_main_super_admin_flag_for_tenant,
        count_active_super_admins,
        get_main_super_admin,
        update_system_user,
    )
    from repositories.tokens_repo import delete_all_tokens_with_user_id
    from schemas.imports import AccountStatus as _AccountStatus
    from schemas.imports import SystemUserRole as _SystemUserRole
    from schemas.system_user_schema import SystemUserUpdate
    from security.password_policy import generate_secure_temp_password
    from services.audit_service import record_audit_event
    from services.tenant_service import retrieve_tenant_by_id

    tenant = await retrieve_tenant_by_id(tenant_id)
    if not getattr(tenant, "is_active", True):
        raise HTTPException(
            status_code=400,
            detail="Cannot replace a super admin on an inactive tenant",
        )

    existing_super_count = await count_active_super_admins(tenant_id)
    if existing_super_count == 0:
        # No existing super_admin to replace — caller wants
        # ``add_super_admin_to_tenant`` instead. We surface a specific
        # error so the frontend can offer the right next step rather
        # than silently doing the wrong thing.
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "This tenant has no active super admin to replace. Use "
                    "POST /v1/admins/tenants/{tenant_id}/super-admins to "
                    "create the first one."
                ),
                "code": "SUPER_ADMIN_NONE_TO_REPLACE",
                "tenant_id": tenant_id,
            },
        )

    old_main = await get_main_super_admin(tenant_id)
    if old_main is None:
        # Multiple active super_admins but none marked main — the
        # backfill will heal this within 6h, but we refuse to add yet
        # another one in the meantime so the operator picks the right
        # flow rather than racing the backfill.
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Tenant has active super admins but no main is currently "
                    "set. Wait for the invariant backfill or use the transfer "
                    "endpoint to designate the main first."
                ),
                "code": "MAIN_SUPER_ADMIN_MISSING",
                "tenant_id": tenant_id,
            },
        )

    raw_password = password or generate_secure_temp_password()
    password_was_generated = password is None

    # 1. Drop the main flag from the old row first so the partial-unique
    # index on (tenant_id, is_main_super_admin=True) does not reject
    # the new insert. Best-effort: if the old row was already cleared
    # by the backfill we still proceed.
    try:
        await clear_main_super_admin_flag_for_tenant(tenant_id)
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "replace_super_admin: clear_main_flag failed tenant=%s",
            tenant_id,
            exc_info=True,
        )

    # 2. Deactivate the old super_admin. We go through the repo directly
    # so guard_system_user_update does not reject the status flip — this
    # is an authority-driven action by an application admin and is
    # logged in the audit trail below.
    now = int(time.time())
    try:
        await update_system_user(
            {"_id": _ObjectId(old_main.id), "tenant_id": tenant_id},
            SystemUserUpdate(
                account_status=_AccountStatus.INACTIVE,
                is_active=False,
                last_updated=now,
            ),
        )
    except Exception as exc:
        # Re-raise the original failure verbatim so the admin sees what
        # went wrong. The new super_admin has NOT been created yet.
        raise HTTPException(
            status_code=500,
            detail=f"Failed to deactivate existing super admin: {exc}",
        )
    try:
        await delete_all_tokens_with_user_id(userId=old_main.id or "")
    except Exception:
        # Best-effort token revocation — failure here just means the old
        # session can keep running until its TTL. The replacement still
        # proceeds.
        pass

    # 3. Create the new super_admin as the main. add_system_user runs
    # the per-plan max_system_users cap, branch validation, permission
    # default assignment, and audit logging for the create itself.
    create_data = SystemUserCreate(
        tenant_id=tenant_id,
        branch_ids=list(branch_ids) if branch_ids else [],
        full_name=full_name,
        email=email,
        role=_SystemUserRole.SUPER_ADMIN,
        account_status=_AccountStatus.ACTIVE,
        is_active=True,
        password_hash=raw_password,
        is_main_super_admin=True,
        must_change_password=password_was_generated,
    )
    new_super = await add_system_user(create_data)

    # 4. Audit the replacement so the trail says "X replaced Y at T".
    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action="system_user.super_admin_replaced",
            resource_type="system_user",
            resource_id=new_super.id or "",
            tenant_id=tenant_id,
            details={
                "replaced_user_id": old_main.id,
                "replaced_email": old_main.email,
                "new_user_id": new_super.id,
                "new_email": new_super.email,
            },
        )
    except Exception:
        pass

    # 5. Welcome email — mirrors the onboarding-accepted flow. Use the
    # same template so the experience is consistent.
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
                template_key="onboarding_accepted",
                context={
                    "full_name": full_name or "there",
                    "platform_name": platform_name,
                    "organization_name": tenant.company_name or "your organization",
                    "admin_email": email,
                    "temp_password": raw_password if password is None else "",
                    "login_url": login_url,
                    "review_notes": (
                        "Your account was provisioned by your platform "
                        "administrator. Sign in with the temporary password "
                        "below and change it from Settings → Account."
                    ),
                },
                dispatch="queued",
            )
        )
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "replace_super_admin: welcome email dispatch failed for %s",
            email,
            exc_info=True,
        )

    return {
        "tenant_id": tenant_id,
        "replaced_user_id": old_main.id,
        "new_super_admin": new_super,
    }


async def add_system_user_from_invite(
    signup_data: SystemUserSignupRequest, tenant_id: str
) -> SystemUserOut:
    """Create a system user from a public-facing invite request.

    - Auto-assigns account_status=ACTIVE
    - Auto-assigns permissions based on role
    - Normalizes and checks email uniqueness

    Role-restriction: the invite path REJECTS ``role=super_admin`` with
    403 ``SUPER_ADMIN_INVITE_FORBIDDEN``. Super_admins are tenant-critical
    (they own billing + main-flag succession) and must be created via the
    dedicated bootstrap or
    ``POST /v1/admins/tenants/{tenant_id}/super-admins`` flow, both of
    which run the is_main_super_admin invariant logic.
    """
    role_str = (
        signup_data.role.value
        if hasattr(signup_data.role, "value")
        else str(signup_data.role)
    )
    if role_str == "super_admin":
        raise HTTPException(
            status_code=403,
            detail={
                "message": (
                    "Super admins cannot be created from the invite endpoint. "
                    "Use POST /v1/admins/tenants/{tenant_id}/super-admins "
                    "(application admin only) so the main-super_admin "
                    "invariant is enforced."
                ),
                "code": "SUPER_ADMIN_INVITE_FORBIDDEN",
            },
        )

    # Build internal SystemUserCreate with system-assigned fields. branch_ids
    # validation + default-branch fallback runs inside add_system_user.
    create_data = SystemUserCreate(
        tenant_id=tenant_id,
        department_id=signup_data.department_id,
        branch_ids=list(signup_data.branch_ids) if signup_data.branch_ids else [],
        full_name=signup_data.full_name,
        email=signup_data.email,
        role=signup_data.role,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        password_hash=signup_data.password,
    )
    return await add_system_user(create_data)


async def _issue_login_tokens(user: SystemUserOut) -> SystemUserOut:
    """Mint access + refresh tokens for an authenticated user."""
    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
        branch_ids=list(getattr(user, "branch_ids", None) or []),
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


async def _continue_login_for_user(user: SystemUserOut) -> Any:
    """Run the post-credential-verification branch: 2FA challenge or token issuance.

    Used by both the single-match Stage-1 path and the Stage-2 ``select-tenant``
    endpoint. Returns either a SystemUserOut (with tokens) or an
    ``otp_required`` dict.
    """
    from services.otp_service import is_mfa_required, create_otp_challenge

    if await is_mfa_required("system_user", user.id):  # type: ignore
        challenge_id, _code = await create_otp_challenge(
            user_id=user.id or "",
            user_type="system_user",  # type: ignore
            role=user.role.value,
            tenant_id=user.tenant_id,
        )
        return {"otp_required": True, "otp_challenge_id": challenge_id}

    return await _issue_login_tokens(user)


async def _build_tenant_options(users: list[SystemUserOut]) -> list[dict]:
    """Resolve tenant company_name for each candidate user, in parallel."""
    from services.tenant_service import retrieve_tenant_by_id

    async def _resolve(user: SystemUserOut) -> dict:
        company_name: str | None = None
        try:
            tenant = await retrieve_tenant_by_id(user.tenant_id)
            company_name = tenant.company_name
        except Exception:
            pass
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        return {
            "tenant_id": user.tenant_id,
            "company_name": company_name,
            "role": role_str,
            "full_name": user.full_name,
            "mfa_enabled": bool(getattr(user, "mfa_enabled", False)),
        }

    return await asyncio.gather(*(_resolve(u) for u in users))


async def authenticate_system_user(
    login_data: SystemUserLogin, tenant_id: str | None = None
) -> Any:
    """Authenticate a system user.

    Resolution rules:

    * If ``tenant_id`` is provided (tenant-scoped login URL), lookup is scoped
      to that tenant — only one record can match. Behaves like the legacy flow.
    * Otherwise, every ``system_users`` record sharing the email is considered.
      Password is verified against each. Among ACTIVE records that match:

      - 0 -> 401 (with email-keyed lockout counter)
      - 1 -> standard 2FA / token-issuance flow
      - 2+ -> tenant-selection challenge (Stage 1 of 2-step login)

    Returns one of:

    * ``SystemUserOut`` with tokens (login completed)
    * ``{"otp_required": True, "otp_challenge_id": ...}``
    * ``{"tenant_selection_required": True, "selection_token": ..., "tenants": [...]}``
    """
    from core.security_policy import get_security_policy
    from security.password_policy import (
        check_login_lockout,
        record_failed_login,
        clear_failed_logins,
    )
    from core.database import db

    policy = await get_security_policy()

    # Lockout is keyed by email so it applies regardless of how many tenants
    # share the address. An attacker can't rotate tenants to bypass it.
    lockout = await check_login_lockout(login_data.email)
    if lockout:
        minutes = lockout["remaining_seconds"] // 60
        raise HTTPException(
            status_code=429,
            detail=(
                "Account temporarily locked due to too many failed login attempts. "
                f"Try again in {minutes} minute(s)."
            ),
        )

    # Tenant-scoped login keeps the single-record path for backward compat.
    if tenant_id:
        raw_records = [
            r
            async for r in db.system_users.find(
                {"email": login_data.email, "tenant_id": tenant_id}
            )
        ]
    else:
        raw_records = await get_raw_system_users_by_email(login_data.email)

    # Verify password against every record. A user might use different
    # passwords for different tenants; we cannot short-circuit.
    matched_raw: list[dict] = [
        r
        for r in raw_records
        if r.get("password_hash")
        and check_password(password=login_data.password, hashed=r["password_hash"])
    ]

    if not matched_raw:
        lockout_status = await record_failed_login(login_data.email, policy=policy)
        if lockout_status.get("locked"):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many failed login attempts. "
                    "Account is temporarily locked for "
                    f"{policy.lockout_duration_minutes} minute(s)."
                ),
            )
        remaining = lockout_status.get("attempts_remaining", "?")
        raise HTTPException(
            status_code=401,
            detail=(
                f"Invalid login credentials. {remaining} attempt(s) "
                "remaining before lockout."
            ),
        )

    # Filter to active accounts. Inactive records are silently dropped so we
    # don't leak whether a tenant has a disabled match for this email.
    active_users: list[SystemUserOut] = []
    for raw in matched_raw:
        user = SystemUserOut(**raw)
        if user.account_status.value == "ACTIVE":
            active_users.append(user)

    if not active_users:
        raise HTTPException(status_code=403, detail="Account is not active")

    await clear_failed_logins(login_data.email)

    # Multi-tenant: issue a selection challenge.
    if len(active_users) > 1:
        from services.tenant_selection_service import create_tenant_selection_challenge

        candidate_ids = [u.id or "" for u in active_users]
        selection_token = await create_tenant_selection_challenge(
            email=login_data.email,
            candidate_user_ids=candidate_ids,
        )
        tenants = await _build_tenant_options(active_users)
        return {
            "tenant_selection_required": True,
            "selection_token": selection_token,
            "tenants": tenants,
        }

    # Exactly one match — proceed straight through to 2FA / tokens.
    return await _continue_login_for_user(active_users[0])


async def complete_login_after_tenant_selection(
    selection_token: str, tenant_id: str
) -> Any:
    """Stage 2 of multi-tenant login.

    Validates and consumes the selection token, resolves the chosen
    ``system_users`` record, and runs the same post-credential-verification
    branch as a single-match Stage-1 login (2FA challenge or token issuance).
    """
    from services.tenant_selection_service import consume_tenant_selection_challenge

    user_id = await consume_tenant_selection_challenge(selection_token, tenant_id)
    user = await get_system_user({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="Selected user not found")
    if user.account_status.value != "ACTIVE":
        raise HTTPException(status_code=403, detail="Account is not active")
    return await _continue_login_for_user(user)


async def authenticate_super_admin_global(login_data: SystemUserLogin) -> dict:
    """Authenticate a super_admin via global (no-tenant) login.

    Returns the standard SystemUserOut plus tenant context info that the
    frontend needs to display the super_admin dashboard:
    - tenant info (company_name, tenant_id)
    - the tenant-scoped login URL path for the tenant management portal

    This is the "administrative" login — the super_admin uses it to view
    tenant metadata, billing, and the tenant login URL.  The tenant-scoped
    login at /system-users/tenant/{tenant_id}/login is used for managing
    the tenant itself (visitors, departments, branding, etc.).

    Email uniqueness is per-tenant, so a super_admin may have the same email
    across multiple tenants. When that happens this endpoint forwards the
    standard tenant-selection challenge so the caller can resolve which
    tenant they meant; the FE then completes via ``/select-tenant``.
    """
    result = await authenticate_system_user(login_data=login_data)

    # If 2FA is required, bubble the OTP challenge up to the route
    if isinstance(result, dict) and result.get("otp_required"):
        return result

    # Multiple tenants share this email — forward the selection challenge so
    # the FE can prompt the user. Stage 2 (/select-tenant) issues real tokens.
    if isinstance(result, dict) and result.get("tenant_selection_required"):
        return result

    if not isinstance(result, SystemUserOut):
        raise HTTPException(status_code=500, detail="Unexpected authentication state")

    user = result

    # Only super_admins get this enriched response
    role_str = user.role.value if hasattr(user.role, "value") else user.role
    if role_str != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="This login endpoint is reserved for tenant super admins",
        )

    # Fetch tenant context
    tenant_info = None
    tenant_login_url = None
    if user.tenant_id:
        try:
            from services.tenant_service import retrieve_tenant_by_id

            tenant = await retrieve_tenant_by_id(user.tenant_id)
            tenant_info = {
                "tenant_id": tenant.id,
                "company_name": tenant.company_name,
            }
            tenant_login_url = f"/v1/system-users/tenant/{user.tenant_id}/login"
        except Exception:
            pass

    return {
        "user": user,
        "tenant": tenant_info,
        "tenant_login_url": tenant_login_url,
    }


async def authenticate_system_user_by_tenant(
    login_data: SystemUserTenantLogin, tenant_id: str
) -> SystemUserOut:
    """Authenticate a system user scoped to a specific tenant (via URL path)."""
    # Validate that the tenant exists
    from services.tenant_service import retrieve_tenant_by_id

    await retrieve_tenant_by_id(tenant_id)

    # Reuse the main auth function with tenant scoping
    login = SystemUserLogin(email=login_data.email, password=login_data.password)
    return await authenticate_system_user(login_data=login, tenant_id=tenant_id)


async def refresh_system_user_tokens(
    refresh_data: SystemUserRefresh, expired_access_token: str
):
    refresh_obj = await get_refresh_tokens(refresh_data.refresh_token)
    if not refresh_obj:
        raise HTTPException(status_code=404, detail="Invalid refresh token")

    if refresh_obj.previousAccessToken != expired_access_token:
        await delete_refresh_token(refreshToken=refresh_data.refresh_token)
        await delete_access_token(accessToken=expired_access_token)
        raise HTTPException(status_code=404, detail="Invalid refresh token")

    user = await get_system_user({"_id": ObjectId(refresh_obj.userId)})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
        branch_ids=list(getattr(user, "branch_ids", None) or []),
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    await delete_access_token(accessToken=expired_access_token)
    await delete_refresh_token(refreshToken=refresh_data.refresh_token)
    return user


async def retrieve_system_user_by_id(user_id: str) -> SystemUserOut:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    result = await get_system_user({"_id": ObjectId(user_id)})
    if not result:
        raise HTTPException(status_code=404, detail="System user not found")
    return result


async def retrieve_system_users(
    tenant_id: str, start=0, stop=100
) -> List[SystemUserOut]:
    return await get_system_users(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def update_system_user_by_id(
    user_id: str, tenant_id: str, user_data: SystemUserUpdate
) -> SystemUserOut:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Snapshot the existing record so we can diff and decide on side-effects
    # (token-record sync, audit payload).
    existing = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not existing:
        raise HTTPException(
            status_code=404, detail="System user not found or update failed"
        )

    # I-1 + I-2: protect the main super_admin row. Raises 403
    # MAIN_SUPER_ADMIN_LOCKED on attempts to change role / account_status
    # / is_active. Caller must transfer first via
    # /v1/system-users/transfer-main-super-admin.
    from services.main_super_admin_guard import guard_system_user_update

    guard_system_user_update(existing=existing, update=user_data)

    # Branch-assignment validation runs in the service layer (it needs DB +
    # plan lookups, which can't sit on the schema validator).
    if user_data.branch_ids is not None:
        user_data.branch_ids = await _resolve_branch_ids_for_user_assignment(
            tenant_id=tenant_id,
            requested=list(user_data.branch_ids),
        )

    result = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id}, user_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="System user not found or update failed"
        )

    # Sync branch_ids onto live access-token records so existing sessions
    # immediately reflect the new branch scope (no wait for token refresh).
    if user_data.branch_ids is not None:
        try:
            from repositories.tokens_repo import update_branch_ids_on_user_access_tokens

            await update_branch_ids_on_user_access_tokens(
                userId=user_id,
                branch_ids=list(result.branch_ids or []),
            )
        except Exception:
            # Token sync is best-effort: stale branch_ids on a token will
            # self-correct at the gate-cache TTL or next refresh.
            pass

    # Audit with a diff so the trail answers "what changed?", not just "X was
    # touched". Mandatory for tenant-scoped writers per the audit rule.
    try:
        changes: dict = {}
        update_dump = user_data.model_dump(exclude_none=True)
        for key, new_val in update_dump.items():
            if key == "last_updated":
                continue
            old_val: Any = getattr(existing, key, None)
            # Normalise enum values for comparison.
            old_cmp = (
                old_val.value
                if old_val is not None and hasattr(old_val, "value")
                else old_val
            )
            new_cmp = (
                new_val.value
                if new_val is not None and hasattr(new_val, "value")
                else new_val
            )
            if old_cmp != new_cmp:
                changes[key] = {"from": old_cmp, "to": new_cmp}
        if changes:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="system_user.updated",
                resource_type="system_user",
                resource_id=user_id,
                tenant_id=tenant_id,
                details={
                    "email": result.email,
                    "role": result.role.value
                    if hasattr(result.role, "value")
                    else result.role,
                    "changes": changes,
                },
            )
    except Exception:
        pass

    return result


async def remove_system_user(user_id: str, tenant_id: str):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Fetch user before deletion for audit logging
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    # I-3: the main super_admin row is never hard-deletable. Distinct from
    # the generic super_admin block below — the LOCKED error makes clear
    # the user must transfer the role first.
    from services.main_super_admin_guard import guard_system_user_delete

    guard_system_user_delete(existing=user)

    # Super admins are tenant-critical. They own billing, branch config, and
    # invite other users — deleting one through the normal user-management
    # surface would orphan the tenant. The application-admin offboarding
    # path (services/tenant_offboarding_service.py) is the only sanctioned
    # way to remove a super_admin.
    role_str = user.role.value if hasattr(user.role, "value") else user.role
    if role_str == "super_admin":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Super admins cannot be removed from user management.",
                "code": "SUPER_ADMIN_DELETE_BLOCKED",
                "hint": (
                    "Use tenant offboarding (application admin only) to remove "
                    "the tenant entirely, or transfer the role first."
                ),
            },
        )

    result = await delete_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id}
    )
    await delete_all_tokens_with_user_id(userId=user_id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="System user not found")

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="system_user.deleted",
            resource_type="system_user",
            resource_id=user_id,
            tenant_id=tenant_id,
            details={
                "email": user.email,
                "role": user.role.value if hasattr(user.role, "value") else user.role,
                "full_name": user.full_name,
            },
        )
    except Exception:
        pass


async def verify_system_user_otp(challenge_id: str, otp_code: str):
    """Step 2 of system user 2FA login — verify OTP and issue tokens."""
    from services.otp_service import verify_otp_challenge

    result = await verify_otp_challenge(challenge_id, otp_code)
    user = await get_system_user({"_id": ObjectId(result["user_id"])})
    if not user:
        raise HTTPException(status_code=401, detail="System user not found")

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
        branch_ids=list(getattr(user, "branch_ids", None) or []),
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


async def toggle_user_mfa(
    user_id: str,
    tenant_id: str,
    mfa_enabled: bool,
) -> SystemUserOut:
    """Allow a tenant user to toggle their own MFA (subject to locks)."""
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    if getattr(user, "mfa_locked_by_admin", False):
        raise HTTPException(
            status_code=403, detail="MFA setting is locked by your administrator"
        )

    from services.tenant_service import retrieve_tenant_by_id

    tenant = await retrieve_tenant_by_id(tenant_id)
    if not getattr(tenant, "mfa_user_override_allowed", True):
        raise HTTPException(
            status_code=403, detail="MFA settings are managed by your administrator"
        )

    update_data = SystemUserUpdate(mfa_enabled=mfa_enabled)
    updated = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        update_data,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update MFA setting")
    return updated


async def admin_set_user_mfa(
    user_id: str,
    tenant_id: str,
    mfa_enabled: bool,
    mfa_locked_by_admin: bool = False,
) -> SystemUserOut:
    """Super admin sets MFA + lock for a tenant user."""
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    update_data = SystemUserUpdate(
        mfa_enabled=mfa_enabled,
        mfa_locked_by_admin=mfa_locked_by_admin,
    )
    updated = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        update_data,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update MFA setting")
    return updated
