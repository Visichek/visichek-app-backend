"""
Unit tests for the MaintenanceModeMiddleware path classification.

These guard the security boundary: when the platform is in maintenance mode
every tenant-facing path must be gated while the application-admin / platform
control plane (and public marketing) stays reachable. The split is derived
from ``config/role_permissions.py`` so these tests also catch accidental
re-classification when permissions change.
"""

from __future__ import annotations

from config.role_permissions import (
    PLATFORM_ONLY_PATH_SEGMENTS,
    TENANT_PATH_SEGMENTS,
)
from core.maintenance_mode import _is_platform_path, _top_segment


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
