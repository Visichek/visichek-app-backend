"""Unit tests for the range-aware, role-scoped Insights endpoint.

The pure helpers (range clamp, auto-granularity, bucket zero-fill, trend) are
exercised directly with no DB. The route test mocks the service so it never
touches Mongo (it passes ?role_view to skip the precompute-cache branch)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from schemas.insights_schema import InsightsMeta, InsightsResponse, Kpi
from services.insights_service import (
    _auto_granularity,
    _bucket_boundaries,
    _resolve_range,
    _trend,
)
from core.errors import AppException


MOCK_SUPER_ADMIN = AuthPrincipal(
    user_id="656f7ac12b9d4f6c9e2b9f7d",
    role="super_admin",
    access_token_id="tok-1",
    jwt_token="jwt-1",
    tenant_id="656f7ac12b9d4f6c9e2b9f7e",
)


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    yield
    app.dependency_overrides.clear()


class TestRangeResolution:
    def test_clamps_start_to_tenant_created_at(self):
        # start (500) is before the tenant existed (1000) -> clamped up.
        start, stop = _resolve_range(1000, 500, 2000, now=3000)
        assert start == 1000
        assert stop == 2000

    def test_defaults_stop_to_now_and_start_to_7d_before(self):
        now = 1_000_000
        start, stop = _resolve_range(0, None, None, now=now)
        assert stop == now
        assert stop - start == 7 * 86400

    def test_stop_before_start_raises_422(self):
        with pytest.raises(AppException) as exc:
            _resolve_range(0, 5000, 1000, now=9000)
        assert exc.value.status_code == 422


class TestAutoGranularity:
    def test_override_wins(self):
        assert _auto_granularity(0, 10**9, "hour") == "hour"

    def test_window_thresholds(self):
        day = 86400
        assert _auto_granularity(0, 24 * 3600, None) == "hour"
        assert _auto_granularity(0, 10 * day, None) == "day"
        assert _auto_granularity(0, 90 * day, None) == "week"
        assert _auto_granularity(0, 400 * day, None) == "month"


class TestBucketBoundaries:
    def test_daily_buckets_are_contiguous_and_zero_fillable(self):
        day = 86400
        start = 0
        stop = 3 * day
        boundaries = _bucket_boundaries(start, stop, "day")
        # 4 day-starts: day 0,1,2,3 all <= stop
        assert len(boundaries) == 4
        starts = [b[0] for b in boundaries]
        # contiguous, evenly spaced
        assert all(starts[i + 1] - starts[i] == day for i in range(len(starts) - 1))

    def test_hour_buckets_label_format(self):
        boundaries = _bucket_boundaries(0, 2 * 3600, "hour")
        assert boundaries[0][1].endswith(":00")
        assert len(boundaries) == 3


class TestTrend:
    def test_up_is_good_when_good_when_up(self):
        t = _trend(120, 100, good_when_up=True)
        assert t.direction == "up"
        assert t.is_good is True
        assert t.change_percent == 20.0

    def test_up_is_bad_when_metric_inverted(self):
        # e.g. no-show rate going up is bad
        t = _trend(30, 10, good_when_up=False)
        assert t.direction == "up"
        assert t.is_good is False

    def test_flat_is_always_good(self):
        t = _trend(50, 50, good_when_up=False)
        assert t.direction == "flat"
        assert t.is_good is True


class TestInsightsRoute:
    @pytest.mark.asyncio
    async def test_insights_returns_envelope(self, cleanup_dependency_overrides):
        from api.v1.dashboard_route import _all_tenant_roles

        app.dependency_overrides[_all_tenant_roles] = lambda: MOCK_SUPER_ADMIN

        fake = InsightsResponse(
            meta=InsightsMeta(
                role_view="super_admin",
                plan_tier="premium",
                available_sections=["traffic", "visitStatus"],
                applied_range={"start": 1, "stop": 2},
            ),
            kpis=[Kpi(key="totalVisits", label="Total visits", value=42)],
            sections={},
        )

        with patch(
            "api.v1.dashboard_route.get_insights",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = fake
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # ?role_view forces the on-demand path (skips precompute cache)
                resp = await client.get(
                    "/v1/dashboard/insights?role_view=super_admin",
                    headers={"Authorization": "Bearer tok-1"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["meta"]["roleView"] == "super_admin"
        assert data["data"]["kpis"][0]["key"] == "totalVisits"
        mock_get.assert_awaited_once()
