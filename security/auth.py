from __future__ import annotations

from typing import Final

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.errors import auth_invalid_token, auth_role_mismatch
from repositories.tokens_repo import get_access_token, get_access_token_allow_expired
from security.principal import ALL_ROLES, AuthPrincipal


token_auth_scheme = HTTPBearer(auto_error=True)
AUTH_ROLES: Final[tuple[str, ...]] = ALL_ROLES
NON_ADMIN_ROLES: Final[tuple[str, ...]] = ("user",)
LEGACY_ROLE_ALIASES: Final[dict[str, str]] = {"member": "user"}


def _normalize_role(role: str | None) -> str:
    value = (role or "").lower()
    return LEGACY_ROLE_ALIASES.get(value, value)


async def _resolve_principal(
    credentials: HTTPAuthorizationCredentials,
    *,
    allow_expired: bool,
) -> AuthPrincipal:
    getter = get_access_token_allow_expired if allow_expired else get_access_token
    token_record = await getter(accessToken=credentials.credentials)
    if token_record is None:
        raise auth_invalid_token()

    role = _normalize_role(token_record.role)
    if role not in AUTH_ROLES:
        raise auth_invalid_token(details={"role": token_record.role})

    return AuthPrincipal(
        user_id=token_record.userId,
        role=role,
        access_token_id=token_record.accesstoken,
        jwt_token=credentials.credentials,
        allow_expired=allow_expired,
        tenant_id=getattr(token_record, "tenant_id", None),
    )


async def verify_any_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await _resolve_principal(credentials, allow_expired=False)


async def verify_user_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(credentials, allow_expired=False)
    if principal.role != "user":
        raise auth_role_mismatch(required_role="user", actual_role=principal.role)
    return principal


async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(credentials, allow_expired=False)
    if principal.role != "admin":
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    return principal


async def verify_token_to_refresh(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await _resolve_principal(credentials, allow_expired=True)


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
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_user_token(credentials)


async def verify_member_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    return await verify_user_refresh_token(principal)


async def verify_token_user_role(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_token(credentials)


async def verify_admin_token_otp(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    return await verify_admin_token(credentials)


# --- VisiChek System User Role Verifiers ---

def verify_system_user_token(*allowed_roles: str):
    """Factory that returns a dependency verifying the token has one of the allowed roles."""
    async def _verifier(
        credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
    ) -> AuthPrincipal:
        principal = await _resolve_principal(credentials, allow_expired=False)
        if principal.role not in allowed_roles:
            raise auth_role_mismatch(
                required_role=",".join(allowed_roles),
                actual_role=principal.role,
            )
        return principal
    return _verifier


async def verify_any_system_user_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    """Verify token belongs to any VisiChek system user role."""
    from security.principal import SYSTEM_USER_ROLES
    principal = await _resolve_principal(credentials, allow_expired=False)
    if principal.role not in SYSTEM_USER_ROLES:
        raise auth_role_mismatch(
            required_role="system_user",
            actual_role=principal.role,
        )
    return principal


async def verify_super_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(credentials, allow_expired=False)
    if principal.role != "super_admin":
        raise auth_role_mismatch(required_role="super_admin", actual_role=principal.role)
    return principal


async def verify_receptionist_token(
    credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
) -> AuthPrincipal:
    principal = await _resolve_principal(credentials, allow_expired=False)
    if principal.role != "receptionist":
        raise auth_role_mismatch(required_role="receptionist", actual_role=principal.role)
    return principal


async def verify_system_user_refresh_token(
    principal: AuthPrincipal = Depends(verify_token_to_refresh),
) -> AuthPrincipal:
    from security.principal import SYSTEM_USER_ROLES
    if principal.role not in SYSTEM_USER_ROLES:
        raise auth_role_mismatch(required_role="system_user", actual_role=principal.role)
    return principal
