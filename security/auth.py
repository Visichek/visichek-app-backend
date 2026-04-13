from __future__ import annotations

from typing import Final, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.errors import auth_invalid_token, auth_role_mismatch
from repositories.tokens_repo import get_access_token, get_access_token_allow_expired
from security.cookie_utils import ACCESS_TOKEN_COOKIE
from security.principal import ALL_ROLES, AuthPrincipal


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
    )


async def verify_any_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await _resolve_principal(request, credentials, allow_expired=False)


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

def verify_system_user_token(*allowed_roles: str):
    """Factory that returns a dependency verifying the token has one of the allowed roles."""
    async def _verifier(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
    ) -> AuthPrincipal:
        principal = await _resolve_principal(request, credentials, allow_expired=False)
        if principal.role not in allowed_roles:
            raise auth_role_mismatch(
                required_role=",".join(allowed_roles),
                actual_role=principal.role,
            )
        return principal
    return _verifier


async def verify_any_system_user_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    """Verify token belongs to any VisiChek system user role."""
    from security.principal import TENANT_USER_ROLES
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role not in TENANT_USER_ROLES:
        raise auth_role_mismatch(
            required_role="system_user",
            actual_role=principal.role,
        )
    return principal


async def verify_super_admin_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "super_admin":
        raise auth_role_mismatch(required_role="super_admin", actual_role=principal.role)
    return principal


async def verify_receptionist_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(request, credentials, allow_expired=False)
    if principal.role != "receptionist":
        raise auth_role_mismatch(required_role="receptionist", actual_role=principal.role)
    return principal


async def verify_system_user_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    from security.principal import TENANT_USER_ROLES
    if principal.role not in TENANT_USER_ROLES:
        raise auth_role_mismatch(required_role="system_user", actual_role=principal.role)
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
