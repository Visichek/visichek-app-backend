from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, Request, status

from core.errors import AppException, ErrorCode, auth_permission_denied
from core.queue.gate_cache import resolve_gate
from schemas.admin_schema import AdminOut
from schemas.imports import AccountStatus, PermissionList
from schemas.user_schema import UserOut
from security.auth import verify_admin_token, verify_user_token
from security.permissions import make_permission_key
from security.principal import AuthPrincipal
from services.admin_service import retrieve_admin_by_admin_id
from services.user_service import retrieve_user_by_user_id

logger = logging.getLogger(__name__)


async def _resolve_cached_admin(user_id: str) -> Optional[AdminOut]:
    """Serve the AdminOut snapshot from the gate cache when available.

    Returns ``None`` on any failure so the caller can fall through to
    the synchronous DB lookup. The cache is refreshed in the background
    by the ``gate.refresh`` task so the next request sees fresh data.
    """
    try:
        gate = await resolve_gate(user_id=user_id, role="admin")
    except Exception:
        logger.warning(
            "gate_cache resolve_gate failed for admin=%s; falling through to DB",
            user_id,
            exc_info=True,
        )
        return None

    if not gate or gate.get("account_type") != "admin":
        return None

    snapshot: Any = gate.get("snapshot")
    if not isinstance(snapshot, dict):
        return None

    try:
        return AdminOut(**snapshot)
    except Exception:
        logger.warning(
            "gate_cache snapshot for admin=%s could not be rehydrated; falling through",
            user_id,
            exc_info=True,
        )
        return None


async def _resolve_cached_user(user_id: str) -> Optional[UserOut]:
    try:
        gate = await resolve_gate(user_id=user_id, role="user")
    except Exception:
        logger.warning(
            "gate_cache resolve_gate failed for user=%s; falling through to DB",
            user_id,
            exc_info=True,
        )
        return None

    if not gate or gate.get("account_type") != "user":
        return None

    snapshot: Any = gate.get("snapshot")
    if not isinstance(snapshot, dict):
        return None

    try:
        return UserOut(**snapshot)
    except Exception:
        logger.warning(
            "gate_cache snapshot for user=%s could not be rehydrated; falling through",
            user_id,
            exc_info=True,
        )
        return None


def _validate_permission_list(permission_list: PermissionList | None) -> None:
    if permission_list is None or not permission_list.permissions:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="No permissions assigned",
        )

    seen: set[str] = set()
    for permission in permission_list.permissions:
        if permission.key:
            if permission.key in seen:
                raise AppException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code=ErrorCode.INTERNAL_ERROR,
                    message="Duplicate permission key configuration",
                    details={"key": permission.key},
                )
            seen.add(permission.key)


def _has_permission(
    *,
    permission_list: PermissionList,
    permission_key: str,
    endpoint_name: str,
    request_method: str,
) -> bool:
    for permission in permission_list.permissions:
        if permission.key and permission.key == permission_key:
            return True

        # Backward compatibility with legacy name+method permissions
        if permission.name == endpoint_name and request_method in permission.methods:
            return True

    return False


def _permission_context(request: Request) -> tuple[str, str, str]:
    endpoint = request.scope.get("endpoint")
    endpoint_name = endpoint.__name__ if endpoint else "unknown"
    request_method = request.method.upper()
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    permission_key = make_permission_key(method=request_method, path=route_path)
    return endpoint_name, request_method, permission_key


async def check_admin_account_status_and_permissions(
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    # Smart gate cache: serves a cached AdminOut snapshot from Redis and
    # enqueues gate.refresh to rebuild the snapshot off the hot path. On
    # any cache failure we fall through to the sync DB lookup so
    # reliability does not regress.
    admin = await _resolve_cached_admin(principal.user_id)
    if admin is None:
        admin = await retrieve_admin_by_admin_id(id=principal.user_id)

    if not admin:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.AUTH_PRINCIPAL_NOT_FOUND,
            message="Admin not found",
        )

    if admin.accountStatus != AccountStatus.ACTIVE:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.AUTH_ACCOUNT_INACTIVE,
            message="Admin account is not active",
        )

    # Application admins are platform operators with full access —
    # skip fine-grained endpoint permission checks.
    return admin


async def check_user_account_status_and_permissions(
    request: Request,
    principal: AuthPrincipal = Depends(verify_user_token),
):
    user = await _resolve_cached_user(principal.user_id)
    if user is None:
        user = await retrieve_user_by_user_id(id=principal.user_id)

    if not user:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.AUTH_PRINCIPAL_NOT_FOUND,
            message="User not found",
        )

    if user.accountStatus != AccountStatus.ACTIVE:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.AUTH_ACCOUNT_INACTIVE,
            message="User account is not active",
        )

    endpoint_name, request_method, permission_key = _permission_context(request)
    permission_list = getattr(user, "permissionList", None)
    _validate_permission_list(permission_list)

    if not _has_permission(
        permission_list=permission_list,  # type: ignore[arg-type]
        permission_key=permission_key,
        endpoint_name=endpoint_name,
        request_method=request_method,
    ):
        raise auth_permission_denied(permission_key)

    return user


async def check_member_account_status_and_permissions(
    request: Request,
    principal: AuthPrincipal = Depends(verify_user_token),
):
    return await check_user_account_status_and_permissions(
        request=request, principal=principal
    )


# --- VisiChek System User Permission Check ---


async def check_system_user_status(
    principal: AuthPrincipal,
):
    """Verify a system user exists and is active. No fine-grained permission check."""
    from repositories.system_user_repo import get_system_user

    system_user = await get_system_user(
        {"_id": __import__("bson").ObjectId(principal.user_id)}
    )
    if not system_user:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.AUTH_PRINCIPAL_NOT_FOUND,
            message="System user not found",
        )
    if system_user.account_status != AccountStatus.ACTIVE:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.AUTH_ACCOUNT_INACTIVE,
            message="System user account is not active",
        )
    return system_user
