"""
Unit tests for the MaintenanceModeMiddleware path classification.

These guard the security boundary: when the platform is in maintenance mode
every tenant-facing path must be gated while the application-admin / platform
control plane (and public marketing) stays reachable. The split is derived
from ``config/role_permissions.py`` so these tests also catch accidental
re-classification when permissions change.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

from config.role_permissions import (
    PLATFORM_ONLY_PATH_SEGMENTS,
    TENANT_PATH_SEGMENTS,
)
from core.maintenance_mode import (
    MaintenanceModeMiddleware,
    _is_platform_path,
    _top_segment,
)


class TestTopSegment:
    def test_extracts_resource_segment(self):
        assert _top_segment("/v1/system-users/{user_id}") == "system-users"

    def test_collection_root(self):
        assert _top_segment("/v1/plans") == "plans"

    def test_non_v1_returns_none(self):
        assert _top_segment("/health") is None

    def test_root_returns_none(self):
        assert _top_segment("/") is None


class TestPlatformPathsStayUp:
    """Paths that must remain reachable during maintenance."""

    def test_admin_login(self):
        assert _is_platform_path("/v1/admins/login") is True

    def test_platform_settings_unified(self):
        assert _is_platform_path("/v1/platform-settings") is True

    def test_admin_platform_settings(self):
        assert _is_platform_path("/v1/admins/platform-settings") is True

    def test_billing_surfaces(self):
        for path in (
            "/v1/plans",
            "/v1/subscriptions",
            "/v1/discounts",
            "/v1/invoices/admin",
            "/v1/payments/webhooks/events",
        ):
            assert _is_platform_path(path) is True, path

    def test_public_marketing(self):
        for path in ("/v1/blogs", "/v1/media", "/v1/faqs", "/v1/pricing-marketing"):
            assert _is_platform_path(path) is True, path

    def test_infra(self):
        for path in ("/health", "/health/ready", "/docs", "/openapi.json", "/"):
            assert _is_platform_path(path) is True, path


class TestTenantPathsAreGated:
    """Tenant operations + public tenant flows must NOT be treated as platform."""

    def test_tenant_login(self):
        assert _is_platform_path("/v1/system-users/login") is False

    def test_public_visitor_flow(self):
        assert _is_platform_path("/v1/visitors/check-in") is False

    def test_public_kiosk_flows(self):
        for path in ("/v1/kyc/scan", "/v1/onboarding/submit"):
            assert _is_platform_path(path) is False, path

    def test_tenant_resources(self):
        for path in (
            "/v1/appointments/",
            "/v1/departments/",
            "/v1/branches",
            "/v1/dashboard/stats",
            "/v1/branding/tenant/abc",
        ):
            assert _is_platform_path(path) is False, path

    def test_overlapping_segments_default_to_tenant(self):
        # tenants / usage / audit-logs appear in BOTH admin and tenant
        # permission lists; for unauthenticated callers they must gate
        # (authenticated app admins pass via the middleware role check).
        for path in ("/v1/tenants/abc", "/v1/usage/my-usage", "/v1/audit-logs/"):
            assert _is_platform_path(path) is False, path

    def test_role_agnostic_unified_endpoints(self):
        # These have no static permission entry; tenants must be gated
        # (app admins reach them via the role short-circuit, not the path).
        for path in ("/v1/sessions", "/v1/auth/change-password", "/v1/notifications"):
            assert _is_platform_path(path) is False, path


class TestSegmentDerivation:
    def test_overlaps_excluded_from_platform_only(self):
        # No segment is both "platform-only" and tenant.
        assert PLATFORM_ONLY_PATH_SEGMENTS.isdisjoint(TENANT_PATH_SEGMENTS)

    def test_admin_segment_is_platform(self):
        assert "admins" in PLATFORM_ONLY_PATH_SEGMENTS

    def test_known_overlaps_are_tenant(self):
        for seg in ("tenants", "usage", "audit-logs"):
            assert seg in TENANT_PATH_SEGMENTS
            assert seg not in PLATFORM_ONLY_PATH_SEGMENTS


def _make_request(method: str, path: str, auth: str | None = None):
    headers: dict[str, str] = {}
    if auth is not None:
        headers["Authorization"] = auth
    return types.SimpleNamespace(
        method=method,
        url=types.SimpleNamespace(path=path),
        headers=headers,
        state=types.SimpleNamespace(request_id="req-1"),
    )


async def _run_dispatch(
    request,
    *,
    maintenance_on: bool = True,
    token_role: str | None = None,
    token_found: bool = True,
):
    """Drive MaintenanceModeMiddleware.dispatch with infra/DB calls mocked.

    Returns (result, sentinel, call_next) so callers can assert whether the
    request passed through (result is sentinel) or was gated (503 response).
    """
    mw = MaintenanceModeMiddleware(app=None)  # type: ignore[arg-type]
    sentinel = object()
    call_next = AsyncMock(return_value=sentinel)

    access_token = None
    if token_found and token_role is not None:
        access_token = types.SimpleNamespace(role=token_role)

    with (
        patch(
            "core.settings.get_settings",
            return_value=types.SimpleNamespace(env="production"),
        ),
        patch(
            "services.platform_settings_service.get_maintenance_state",
            new=AsyncMock(return_value={"mode": maintenance_on, "message": "down"}),
        ),
        patch(
            "core.maintenance_mode.get_access_token_allow_expired",
            new=AsyncMock(return_value=access_token),
        ),
    ):
        result = await mw.dispatch(request, call_next)
    return result, sentinel, call_next


class TestDispatchGating:
    """End-to-end branching of the maintenance gate (token type + method)."""

    async def test_maintenance_off_passes_everything(self):
        req = _make_request("POST", "/v1/visitors/check-in")
        result, sentinel, call_next = await _run_dispatch(req, maintenance_on=False)
        assert result is sentinel
        call_next.assert_awaited_once()

    async def test_app_admin_token_bypasses_writes(self):
        req = _make_request("POST", "/v1/appointments", auth="Bearer tok")
        result, sentinel, _ = await _run_dispatch(req, token_role="admin")
        assert result is sentinel

    async def test_tenant_token_blocked_on_write(self):
        req = _make_request("POST", "/v1/appointments", auth="Bearer tok")
        result, sentinel, call_next = await _run_dispatch(
            req, token_role="receptionist"
        )
        assert result is not sentinel
        assert result.status_code == 503
        call_next.assert_not_awaited()

    async def test_tenant_token_blocked_on_read(self):
        # Tenant tokens are locked out on EVERY method, GET included.
        req = _make_request("GET", "/v1/appointments", auth="Bearer tok")
        result, sentinel, _ = await _run_dispatch(req, token_role="super_admin")
        assert result is not sentinel
        assert result.status_code == 503

    async def test_unrecognised_token_blocked(self):
        # A token that fails lookup is treated as a (non-admin) tenant caller.
        req = _make_request("GET", "/v1/appointments", auth="Bearer tok")
        result, sentinel, _ = await _run_dispatch(req, token_found=False)
        assert result is not sentinel
        assert result.status_code == 503

    async def test_unauthenticated_get_passes(self):
        req = _make_request("GET", "/v1/visitors/check-in")
        result, sentinel, _ = await _run_dispatch(req)
        assert result is sentinel

    async def test_unauthenticated_write_blocked(self):
        req = _make_request("POST", "/v1/visitors/check-in")
        result, sentinel, call_next = await _run_dispatch(req)
        assert result is not sentinel
        assert result.status_code == 503
        call_next.assert_not_awaited()

    async def test_platform_path_bypasses_anonymous_post(self):
        # Admin login is an unauthenticated POST on a platform-only segment —
        # it must survive maintenance so admins can get in and toggle it off.
        req = _make_request("POST", "/v1/admins/login")
        result, sentinel, _ = await _run_dispatch(req)
        assert result is sentinel
