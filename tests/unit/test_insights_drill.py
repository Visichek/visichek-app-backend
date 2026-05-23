"""Unit tests for the Insights drill-down (records behind a chart element).

Pure key/window parsing is tested DB-free; the two routes are tested with the
service mocked so they never touch Mongo."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from services.insights_drill_service import _bucket_window, _hour_of


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    yield
    app.dependency_overrides.clear()


class TestBucketWindow:
    def test_day_bucket_span(self):
        start, stop = _ts(2026, 1, 1), _ts(2026, 12, 31)
        win = _bucket_window("2026-05-10", "day", start, stop)
        assert win is not None
        lo, hi = win
        assert lo == _ts(2026, 5, 10)
        assert hi == _ts(2026, 5, 11)  # +1 day

    def test_week_bucket_span(self):
        start, stop = _ts(2026, 1, 1), _ts(2026, 12, 31)
        win = _bucket_window("2026-05-10", "week", start, stop)
        assert win is not None
        assert win[1] - win[0] == 7 * 86400

    def test_clamped_to_range(self):
        # bucket starts before range -> clamped up to range start
        start, stop = _ts(2026, 5, 10), _ts(2026, 5, 20)
        win = _bucket_window("2026-05-09", "day", start, stop)
        assert win is not None
        assert win[0] == start  # clamped

    def test_non_date_key_returns_none(self):
        assert _bucket_window("17:00", "day", 0, 10**9) is None


class TestHourOf:
    def test_parses_hour_label(self):
        assert _hour_of("17:00") == 17

    def test_parses_bare_int(self):
        assert _hour_of("9") == 9

    def test_rejects_out_of_range_and_garbage(self):
        assert _hour_of("30") is None
        assert _hour_of("abc") is None


class TestTenantDrillRoute:
    @pytest.mark.asyncio
    async def test_returns_rows_envelope(self, cleanup_dependency_overrides):
        from api.v1.dashboard_route import _all_tenant_roles

        principal = AuthPrincipal(
            user_id="u1", role="super_admin", access_token_id="t",
            jwt_token="jwt", tenant_id="tenant-1",
        )
        app.dependency_overrides[_all_tenant_roles] = lambda: principal

        payload = {
            "columns": ["visitorName", "status", "checkInTime"],
            "rows": [{"visitorName": "Jane", "status": "checked_in", "checkInTime": 123}],
            "total": 1,
        }
        with patch(
            "services.insights_drill_service.drill_tenant",
            new_callable=AsyncMock,
        ) as mock_drill:
            mock_drill.return_value = payload
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/v1/dashboard/insights/drill?section=traffic&key=2026-05-10",
                    headers={"Authorization": "Bearer t"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 1
        assert data["data"]["rows"][0]["visitorName"] == "Jane"
        # the route forwards section/key + scoping to the service
        assert mock_drill.await_args.kwargs["section"] == "traffic"
        assert mock_drill.await_args.kwargs["caller_role"] == "super_admin"


class TestAdminDrillRoute:
    @pytest.mark.asyncio
    async def test_returns_rows_envelope(self, cleanup_dependency_overrides):
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = (
            lambda: MagicMock()
        )

        payload = {
            "columns": ["companyName", "planName", "country", "signedUpAt"],
            "rows": [{"companyName": "Acme", "planName": "Premium", "country": "NG", "signedUpAt": 1}],
            "total": 1,
        }
        with patch(
            "services.insights_drill_service.drill_admin",
            new_callable=AsyncMock,
        ) as mock_drill:
            mock_drill.return_value = payload
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/v1/admins/dashboard/insights/drill?section=tenantSignups&key=2026-05-10",
                    headers={"Authorization": "Bearer t"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["rows"][0]["companyName"] == "Acme"
        assert mock_drill.await_args.kwargs["section"] == "tenantSignups"
