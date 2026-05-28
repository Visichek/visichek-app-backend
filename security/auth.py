from __future__ import annotations

from typing import Final, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.errors import (
    AppException,
    ErrorCode,
    auth_invalid_token,
    auth_role_mismatch,
)
from repositories.tokens_repo import get_access_token, get_access_token_allow_expired
from schemas.imports import AccountStatus
from security.cookie_utils import ACCESS_TOKEN_COOKIE
from security.principal import ALL_ROLES, AuthPrincipal, TENANT_USER_ROLES


# ---------------------------------------------------------------------------
# must_change_password enforcement
#
# When a system_user row carries ``must_change_password=True`` (set by the
# onboarding-accept flow when it auto-generates a temporary password, by
# replace-super-admin under the same condition, and by every
# authority-driven password reset), the gate refuses every request EXCEPT
# the self-service change-password endpoint until the user picks their own
# password. The login response surfaces the flag so the frontend can route
# straight to the change-password screen.
#
# Path matching is exact (route path with placeholders, NOT the literal
# URL) because FastAPI populates ``request.scope["route"].path`` with the
# template before any URL params are substituted.
# ---------------------------------------------------------------------------

_PASSWORD_CHANGE_ALLOWED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/v1/auth/change-password",
        "/v1/system-users/change-password",
        "/v1/admins/change-password",
    }
)


# ---------------------------------------------------------------------------
# tenant-agreement acceptance enforcement
#
# Application admins maintain two master legal documents — the Data Processing
# Agreement and the Visitor Privacy Policy — that every tenant must accept at
# their current published version to keep using the service. Publishing a new
# version forces re-acceptance.
#
# The gate is INTENTIONALLY NARROW: it does NOT block login, the
# acceptance/profile/settings/session endpoints, or any read. It blocks ONLY
# the core operational WRITES (creating appointments, submitting/processing
# visitor information, check-ins, badges) — the day-to-day "using the service"
# actions — until the tenant's super_admin accepts. So a blocked tenant can
# still sign in, see what is pending, accept it, manage their account, and
# browse — they just can't run visitor operations until they agree.
#
# Matched by route-path PREFIX (the template, not the literal URL) + method.
# ---------------------------------------------------------------------------

#: Operational surfaces whose WRITES are gated until agreements are accepted.
#: Reads (GET/HEAD/OPTIONS) on these paths are never blocked.
_AGREEMENT_GATED_PREFIXES: Final[tuple[str, ...]] = (
    "/v1/appointments",  # create / manage appointments
    "/v1/visitors",  # visitor records
    "/v1/visitor-profiles",  # visitor identity profiles
    "/v1/visitor-verification",  # ID verification
    "/v1/checkins",  # check-in approval / lifecycle
    "/v1/checkin-configs",  # kiosk config + authenticated check-in submit
    "/v1/badges",  # badge issuance
)

_AGREEMENT_GATED_METHODS: Final[frozenset[str]] = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)


def _is_agreement_gated(method: str, route_path: str) -> bool:
    """True only for a WRITE to a core operational surface."""
    if method.upper() not in _AGREEMENT_GATED_METHODS:
        return False
    return any(route_path.startswith(p) for p in _AGREEMENT_GATED_PREFIXES)


def _raise_agreement_acceptance_required(pending: list[str]) -> None:
    raise AppException(
        status_code=403,
        code=ErrorCode.AUTH_PERMISSION_DENIED,
        message=(
            "Your organization must accept the latest platform agreements before "
            "running visitor operations. A super admin can review and accept them "
            "under GET /v1/agreements."
        ),
        details={
            "code": "AGREEMENT_ACCEPTANCE_REQUIRED",
            "pending": pending,
            "allowed_endpoints": ["/v1/agreements", "/v1/agreements/{key}/accept"],
        },
    )


async def _enforce_agreement_acceptance(
    request: Request, principal: AuthPrincipal
) -> None:
    """Block core operational writes until the tenant accepts current agreements.

    Only fires for WRITES to the gated operational surfaces
    (``_AGREEMENT_GATED_PREFIXES``); login, acceptance, profile/settings/session
    endpoints and all reads pass through untouched. Fail-open on any error so a
    Redis/Mongo blip never locks a tenant out.
    """
    tenant_id = principal.tenant_id
    if not tenant_id:
        return
    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or request.url.path
    if not _is_agreement_gated(request.method, route_path):
        return
    try:
        from services.tenant_agreement_service import agreements_gate_check

        ok, pending = await agreements_gate_check(tenant_id)
    except Exception:
        return  # fail-open
    if not ok:
        _raise_agreement_acceptance_required(pending)


async def _fetch_account_flags(user_id: str, *, collection: str) -> dict:
    """Read security-relevant flags for a user/admin in one projected query.

    Returns the ``must_change_password`` and ``account_status`` fields so
    both the password-change gate and the account-status gate share a
    single DB round-trip. Returns ``{}`` on any DB error so a Mongo blip
    never locks every account out (fail-open); the next successful read
    re-applies the gate on the offending row.
    """
    try:
        from bson import ObjectId

        from core.database import db

        doc = await db[collection].find_one(
            {"_id": ObjectId(user_id)},
            projection={"must_change_password": 1, "account_status": 1},
        )
    except Exception:
        return {}
    return doc or {}


def _is_active_account(raw_status: object) -> bool:
    """True when a stored ``account_status`` value represents an active account.

    Treats a missing field as active (legacy rows pre-dating the column
    must keep working). Accepts both the raw string and an enum member.
    """
    if raw_status is None:
        return True
    value = getattr(raw_status, "value", raw_status)
    return str(value).upper() == AccountStatus.ACTIVE.value


def _raise_account_inactive() -> None:
    raise AppException(
        status_code=403,
        code=ErrorCode.AUTH_ACCOUNT_INACTIVE,
        message="Account is not active",
    )


def _raise_password_change_required() -> None:
    raise AppException(
        status_code=403,
        code=ErrorCode.AUTH_PERMISSION_DENIED,
        message=(
            "Password change required. Submit a new password via "
            "POST /v1/auth/change-password before using the rest of the API."
        ),
        details={
            "code": "PASSWORD_CHANGE_REQUIRED",
            "allowed_endpoints": sorted(_PASSWORD_CHANGE_ALLOWED_PATHS),
        },
    )


async def _enforce_account_gates(request: Request, principal: AuthPrincipal) -> None:
    """Enforce per-row account gates on every authenticated request.

    Two gates, served by a single projected DB read:

    1. **Account status** — a tenant user (row in ``system_users``)
       whose ``account_status`` is no longer ACTIVE is refused with 403
       ``AUTH_ACCOUNT_INACTIVE``, even on the change-password endpoints.
       This is defence-in-depth alongside token revocation on
       deactivation: a token that outlived its revocation, or a refresh
       that slipped through, is still rejected here.
    2. **Forced password change** — when the row carries
       ``must_change_password=True`` every request EXCEPT the
       change-password endpoints (``_PASSWORD_CHANGE_ALLOWED_PATHS``) is
       refused so the user is funnelled into picking a new password.

    Applies to tenant-user roles (``system_users``) and application
    admins (``admins``). Application users (role ``user``) are out of
    scope — they pick their own password at signup and have no
    admin-driven create path, and active-status for them is enforced by
    the ``check_user_account_status_and_permissions`` gate.

    Fail-open on DB error (``_fetch_account_flags`` returns ``{}``).
    """
    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or request.url.path
    change_password_path = route_path in _PASSWORD_CHANGE_ALLOWED_PATHS

    if principal.role in TENANT_USER_ROLES:
        flags = await _fetch_account_flags(principal.user_id, collection="system_users")
        if not _is_active_account(flags.get("account_status")):
            _raise_account_inactive()
        if not change_password_path and flags.get("must_change_password"):
            _raise_password_change_required()
        await _enforce_agreement_acceptance(request, principal)
        return

    if principal.role == "admin":
        if change_password_path:
            return
        flags = await _fetch_account_flags(principal.user_id, collection="admins")
        if flags.get("must_change_password"):
            _raise_password_change_required()
        return


# Backwards-compatible alias — older call sites / tests referenced the
# password-only name before account-status enforcement was folded in.
_enforce_must_change_password = _enforce_account_gates


# auto_error=False so missing header doesn't 403 before we check cookies
token_auth_scheme = HTTPBearer(auto_error=False)
AUTH_ROLES: Final[tuple[str, ...]] = ALL_ROLES
NON_ADMIN_ROLES: Final[tuple[str, ...]] = ("user",)
APP_ROLE_ALIASES: Final[dict[str, str]] = {"member": "user"}


def _normalize_role(role: str | None) -> str:
    value = (role or "").lower()
    return APP_ROLE_ALIASES.get(value, value)


def _extract_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
) -> str:
    """Get JWT string from Bearer header first, then fall back to cookie."""
    if credentials:
        return credentials.credentials
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if token:
        return token
    raise auth_invalid_token()


async def _resolve_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    *,
    allow_expired: bool,
) -> AuthPrincipal:
    jwt_token = _extract_token(request, credentials)
    getter = get_access_token_allow_expired if allow_expired else get_access_token
    token_record = await getter(accessToken=jwt_token)
    if token_record is None:
        raise auth_invalid_token()

    role = _normalize_role(token_record.role)
    if role not in AUTH_ROLES:
        raise auth_invalid_token(details={"role": token_record.role})

    from typing import cast as _cast
    from security.principal import AllRolesLiteral as _AllRolesLiteral

    return AuthPrincipal(
        user_id=token_record.userId,
        role=_cast(_AllRolesLiteral, role),
        access_token_id=token_record.accesstoken or "",
        jwt_token=jwt_token,
        allow_expired=allow_expired,
        tenant_id=getattr(token_record, "tenant_id", None),
        department_id=getattr(token_record, "department_id", None),
        branch_ids=list(getattr(token_record, "branch_ids", None) or []),
    )


async def verify_any_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    await _enforce_must_change_password(request, principal)
    return principal


async def verify_user_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "user":
        raise auth_role_mismatch(required_role="user", actual_role=principal.role)
    return principal


async def verify_admin_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "admin":
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    await _enforce_must_change_password(request, principal)
    return principal


async def verify_token_to_refresh(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await _resolve_principal(request, credentials, allow_expired=True)


async def verify_user_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    if principal.role != "user":
        raise auth_role_mismatch(required_role="user", actual_role=principal.role)
    return principal


async def verify_admin_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    if principal.role != "admin":
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    return principal


# Backward-compatible aliases
async def verify_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_user_token(request, credentials)


async def verify_member_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    return await verify_user_refresh_token(principal)


async def verify_token_user_role(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_token(request, credentials)


async def verify_admin_token_otp(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_admin_token(request, credentials)


# --- VisiChek System User Role Verifiers ---


_VERIFY_SYSTEM_USER_TOKEN_CACHE: dict = {}


def verify_system_user_token(*allowed_roles: str):
    """Factory that returns a dependency verifying the token has one of the allowed roles.

    Closures are cached by the (sorted) set of allowed roles so that repeated calls with
    the same roles return the SAME dependency function. This is important for FastAPI's
    ``app.dependency_overrides`` to work reliably (e.g. in tests), since override lookup
    is keyed on object identity.
    """
    key = tuple(sorted(set(allowed_roles)))
    cached = _VERIFY_SYSTEM_USER_TOKEN_CACHE.get(key)
    if cached is not None:
        return cached

    async def _verifier(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(
            token_auth_scheme
        ),
    ) -> AuthPrincipal:
        # Allow tests to override the factory itself: if the app has registered
        # an override for verify_system_user_token, call it instead. This lets
        # tests do app.dependency_overrides[verify_system_user_token] = lambda: principal
        try:
            app = request.app
            override = app.dependency_overrides.get(verify_system_user_token)
        except Exception:
            override = None
        if override is not None:
            import inspect

            try:
                # Support lambda/fn with no args, *roles, or (request, credentials)
                sig = inspect.signature(override)
                params = sig.parameters
                if len(params) == 0:
                    result = override()
                elif any(
                    p.kind == inspect.Parameter.VAR_POSITIONAL for p in params.values()
                ):
                    result = override(*allowed_roles)
                else:
                    # Best-effort: try no args, fallback to roles
                    try:
                        result = override()
                    except TypeError:
                        result = override(*allowed_roles)
            except (TypeError, ValueError):
                result = override() if callable(override) else override
            if inspect.isawaitable(result):
                result = await result
            return result

        principal = await _resolve_principal(request, credentials, allow_expired=False)
        if principal.role not in allowed_roles:
            raise auth_role_mismatch(
                required_role=",".join(allowed_roles),
                actual_role=principal.role,
            )
        await _enforce_must_change_password(request, principal)
        await _capture_location(request, principal)
        return principal

    _VERIFY_SYSTEM_USER_TOKEN_CACHE[key] = _verifier
    return _verifier


async def verify_any_system_user_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    """Verify token belongs to any VisiChek system user role."""
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role not in TENANT_USER_ROLES:
        raise auth_role_mismatch(
            required_role="system_user",
            actual_role=principal.role,
        )
    await _enforce_must_change_password(request, principal)
    await _capture_location(request, principal)
    return principal


async def _capture_location(request: Request, principal: AuthPrincipal) -> None:
    """Piggyback location capture on system-user auth.

    Kept as a thin wrapper that imports lazily so a failure here never
    touches auth resolution. The actual enqueue lives in
    :mod:`security.account_status_check` to avoid pulling the queue
    pipeline into every auth module at import time.
    """
    try:
        from security.account_status_check import capture_user_location_from_request

        await capture_user_location_from_request(
            request,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            role=principal.role,
        )
    except Exception:
        # Auth path must never fail because location capture failed.
        pass


async def verify_super_admin_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "super_admin":
        raise auth_role_mismatch(
            required_role="super_admin", actual_role=principal.role
        )
    await _enforce_account_gates(request, principal)
    return principal


async def verify_tenant_form_configure_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    """Permission gate for tenant-form mutations (Issue 3 backend).

    Mirrors the frontend ``TENANT_FORM_CONFIGURE`` capability:
    super_admin and dept_admin can create / draft / update / publish /
    archive / clone tenant forms (visitor check-in + appointment
    templates). Every other tenant role — receptionist, auditor,
    security_officer, dpo — gets ``AUTH_ROLE_MISMATCH`` so the route
    is closed both at the auth layer and on the frontend.

    Reads (list, by-target, get-one, active-for-target) keep the
    looser ``verify_any_system_user_token`` gate since the
    receptionist needs to render the published form on the kiosk and
    the auditor needs to inspect what's published.
    """
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role not in ("super_admin", "dept_admin"):
        raise auth_role_mismatch(
            required_role="super_admin_or_dept_admin",
            actual_role=principal.role,
        )
    await _enforce_account_gates(request, principal)
    await _capture_location(request, principal)
    return principal


async def verify_optional_kiosk_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> Optional[AuthPrincipal]:
    """Soft auth for the public kiosk submit endpoints.

    Returns the resolved ``AuthPrincipal`` when a valid Bearer / cookie
    token is presented, or ``None`` when no token is present. The
    service layer is responsible for deciding whether unauthenticated
    access is allowed for the tenant's plan — on Free / Starter the
    public submit is closed and only a system user with visitor
    permissions (super_admin / dept_admin / receptionist) may drive
    the kiosk. On plans that grant ``/v1/public/tenants/*/submit`` no
    token is needed and ``None`` is returned to keep the kiosk usable
    without a login.

    Any invalid / expired token surfaces the usual
    ``AUTH_INVALID_TOKEN`` error so a misconfigured kiosk doesn't
    silently fall back to anonymous mode.
    """
    # No header AND no cookie — return None silently and let the
    # service layer decide whether anonymous is allowed.
    if credentials is None and not request.cookies.get(ACCESS_TOKEN_COOKIE):
        return None
    return await _resolve_principal(request, credentials, allow_expired=False)


async def verify_receptionist_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "receptionist":
        raise auth_role_mismatch(
            required_role="receptionist", actual_role=principal.role
        )
    await _enforce_account_gates(request, principal)
    return principal


async def verify_system_user_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    from security.principal import TENANT_USER_ROLES

    if principal.role not in TENANT_USER_ROLES:
        raise auth_role_mismatch(
            required_role="system_user", actual_role=principal.role
        )
    return principal


async def verify_any_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    """Role-agnostic refresh-token verifier.

    Accepts any of the 8 roles (admin, user, or any tenant user role) and returns
    the principal. The caller is expected to dispatch on `principal.role` to invoke
    the role-specific refresh service.
    """
    if principal.role not in ALL_ROLES:
        raise auth_invalid_token(details={"role": principal.role})
    return principal
