from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from schemas.imports import Permission, PermissionList

logger = logging.getLogger(__name__)


def make_permission_key(*, method: str, path: str) -> str:
    normalized_path = "/" + "/".join(
        segment for segment in path.strip("/").split("/") if segment
    )
    return f"{method.upper()}:{normalized_path}"


def _route_permissions(
    router: APIRouter, *, methods: set[str] | None = None
) -> PermissionList:
    permissions: list[Permission] = []
    seen_keys: set[str] = set()

    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue

        route_methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for method in route_methods:
            if methods and method not in methods:
                continue

            key = make_permission_key(method=method, path=route.path)
            if key in seen_keys:
                raise ValueError(f"Duplicate permission key detected: {key}")
            seen_keys.add(key)

            permissions.append(
                Permission(
                    name=route.endpoint.__name__,
                    methods=[method],
                    path=route.path,
                    key=key,
                    description=route.description,
                )
            )

    return PermissionList(permissions=permissions)


def get_router_permissions(router: APIRouter) -> PermissionList:
    return _route_permissions(router)


def get_router_get_permissions(router: APIRouter) -> PermissionList:
    return _route_permissions(router, methods={"GET"})


def default_get_permissions() -> PermissionList:
    from api.v1.admin_route import router

    return get_router_get_permissions(router)


def default_permissions() -> PermissionList:
    from api.v1.admin_route import router

    return get_router_permissions(router)


def find_unregistered_admin_routes(app: FastAPI) -> list[str]:
    """Return permission keys for admin-gated routes missing from config.

    Walks the live ``FastAPI`` app and identifies every route whose
    dependency tree includes
    ``check_admin_account_status_and_permissions``. Diffs the resulting
    ``METHOD:/path`` keys against ``config.role_permissions.ADMIN_PERMISSIONS``
    and returns any keys absent from the static config.

    Use case: call from app lifespan / a unit test so the next time a
    new admin route lands without a matching ``ADMIN_PERMISSIONS`` entry,
    it fails loud rather than silently 403-ing every admin in
    production. Returns an empty list when coverage is complete.
    """
    from config.role_permissions import ADMIN_PERMISSIONS
    from security.account_status_check import (
        check_admin_account_status_and_permissions,
    )

    def _dep_uses_admin_gate(dep) -> bool:
        if getattr(dep.call, "__name__", "") == check_admin_account_status_and_permissions.__name__:
            return True
        return any(_dep_uses_admin_gate(sub) for sub in dep.dependencies)

    registered = {p.key for p in ADMIN_PERMISSIONS if p.key}
    missing: list[str] = []
    seen_keys: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not any(_dep_uses_admin_gate(d) for d in route.dependant.dependencies):
            continue
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for method in methods:
            key = make_permission_key(method=method, path=route.path)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if key not in registered:
                missing.append(key)
    return sorted(missing)


def assert_admin_permission_coverage(app: FastAPI) -> None:
    """Log a warning for any admin-gated route missing from config.

    Non-fatal — production should keep serving even when coverage drifts
    so a forgotten registration does not take the platform offline.
    The self-heal on the gate check (``heal_admin_permissions_to_preset``)
    only helps once a key is in ``ADMIN_PERMISSIONS``, so this warning
    is what tells operators they still need to backfill.
    """
    missing = find_unregistered_admin_routes(app)
    if not missing:
        return
    logger.warning(
        "Admin permission coverage gap: %d admin-gated route(s) are not "
        "registered in config.role_permissions.ADMIN_PERMISSIONS. Every "
        "admin (including all_controls) will 403 on these endpoints "
        "until the keys are added. Missing keys: %s",
        len(missing),
        ", ".join(missing),
    )
