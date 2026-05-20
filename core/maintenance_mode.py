"""
MaintenanceModeMiddleware
=========================
When an application admin flips the platform into maintenance mode (the only
runtime-editable platform setting — see ``services/platform_settings_service``)
every TENANT-facing request is locked out with HTTP 503 ``MAINTENANCE_MODE``,
while the application-admin / platform control plane stays up so admins can
still log in and toggle maintenance back off.

"Tenant-facing" is derived from the role-permission config
(``config/role_permissions.py``) rather than a hand-maintained URL list: any
route whose resource segment belongs only to the admin/user surface (admin
login, billing, platform settings, public marketing) stays reachable; every
authenticated tenant role and every unauthenticated public tenant flow
(visitor check-in, KYC kiosk, onboarding submission, tenant login) is blocked.

Runs ahead of ``PlanEnforcementMiddleware`` so a tenant request short-circuits
before any plan/quota work. Reads the maintenance flag through a short-TTL
Redis cache and fails OPEN — a Redis/Mongo blip resolves to "not in
maintenance" so infra trouble can never strand the platform.
"""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from config.role_permissions import PLATFORM_ONLY_PATH_SEGMENTS
from core.response_envelope import error_response
from repositories.tokens_repo import get_access_token_allow_expired
from security.principal import APP_ROLES

logger = logging.getLogger(__name__)

# Infrastructure paths that must stay reachable regardless of maintenance
# (health probes, API docs). These have no ``/v1`` resource segment.
_INFRA_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")


def _top_segment(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "v1":
        return parts[1]
    return None


def _is_platform_path(path: str) -> bool:
    """True for infra + the application-admin / platform control plane (and
    public marketing) — i.e. anything that is NOT a tenant operation."""
    if path == "/":
        return True
    if any(path == p or path.startswith(p) for p in _INFRA_PREFIXES):
        return True
    seg = _top_segment(path)
    return seg is not None and seg in PLATFORM_ONLY_PATH_SEGMENTS


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from core.settings import get_settings

        if get_settings().env == "testing":
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Platform control-plane + infra paths are always reachable, so we
        # don't even pay the maintenance lookup for them.
        if _is_platform_path(path):
            return await call_next(request)

        from services.platform_settings_service import get_maintenance_state

        maintenance = await get_maintenance_state()
        if not maintenance.get("mode"):
            return await call_next(request)

        # Maintenance is ON and this is a tenant-facing path. Application
        # admins/users still get through (they manage the platform); every
        # tenant role and every unauthenticated public tenant flow is locked
        # out.
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", maxsplit=1)[1]
            try:
                access_token = await get_access_token_allow_expired(accessToken=token)
            except Exception:
                access_token = None
            if access_token and (access_token.role or "").lower() in APP_ROLES:
                return await call_next(request)

        return error_response(
            status_code=503,
            message="Platform under maintenance",
            data={
                "code": "MAINTENANCE_MODE",
                "details": (
                    maintenance.get("message")
                    or "The platform is temporarily unavailable for scheduled "
                    "maintenance. Please try again shortly."
                ),
            },
            headers={"Retry-After": "300"},
            request_id=getattr(request.state, "request_id", None),
        )
