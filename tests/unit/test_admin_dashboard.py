"""Unit tests for the application admin platform-wide dashboard."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from schemas.admin_dashboard_schema import (
    AdminDashboardStats,
    PlanDistribution,
    TopTenantByIncidents,
    SubscriptionStatusBreakdown,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestAdminDashboardSchema:
    def test_defaults(self):
        stats = AdminDashboardStats()
        assert stats.total_tenants == 0
        assert stats.active_tenants == 0
        assert stats.total_tenant_users == 0
        assert stats.total_subscriptions == 0
        assert stats.subscription_breakdown.active == 0
        assert stats.plan_distribution == []
        assert stats.total_incidents == 0
        assert stats.total_monthly_revenue == 0.0
        assert stats.last_updated > 0

    def test_plan_distribution(self):
        pd = PlanDistribution(
            plan_id="abc",
            plan_name="Pro",
            plan_tier="professional",
            subscriber_count=10,
        )
        assert pd.subscriber_count == 10

    def test_top_tenant_by_incidents(self):
        t = TopTenantByIncidents(
            tenant_id="t1",
            company_name="Acme",
            incident_count=5,
        )
        assert t.incident_count == 5

    def test_subscription_breakdown(self):
        sb = SubscriptionStatusBreakdown(active=10, trialing=2, cancelled=1)
        assert sb.active == 10
        assert sb.expired == 0


# ---------------------------------------------------------------------------
# Service tests (mocked DB)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Chainable async cursor stand-in (supports .sort().limit() then async-for)."""

    def __init__(self, items):
        self._iter = _AsyncIter(items)

    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def __aiter__(self):
        return self._iter


class _FakeCollection:
    """Lightweight stand-in for a Motor collection."""

    def __init__(self, count_val=0, find_one_val=None, agg_results=None):
        self._count_val = count_val
        self._find_one_val = find_one_val
        self._agg_results = agg_results or []

    async def count_documents(self, *a, **kw):
        return self._count_val

    async def find_one(self, *a, **kw):
        return self._find_one_val

    def aggregate(self, *a, **kw):
        return _AsyncIter(self._agg_results)

    def find(self, *a, **kw):
        return _FakeCursor([])


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class _FakeDB:
    """Stand-in for the Motor database.

    The admin-dashboard service mixes attribute access (``db.system_users``)
    and item access (``db[collection]``); both must resolve to a collection,
    otherwise an auto-generated ``MagicMock`` leaks into ``asyncio.gather``
    and raises ``TypeError: ... awaitable is required``.
    """

    def __init__(self, col):
        self._col = col

    def __getitem__(self, name):
        return self._col

    def __getattr__(self, name):
        return self._col


@pytest.mark.asyncio
async def test_get_admin_dashboard_stats_returns_stats():
    """Service returns AdminDashboardStats with aggregated data."""
    # Default collection returns 5 for every count and empty aggregates.
    default_col = _FakeCollection(count_val=5)
    fake_db = _FakeDB(default_col)

    with patch("services.admin_dashboard_service.db", fake_db):
        from services.admin_dashboard_service import get_admin_dashboard_stats

        stats = await get_admin_dashboard_stats()
        assert isinstance(stats, AdminDashboardStats)
        assert stats.total_tenants == 5


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_dashboard_route_returns_200():
    """GET /v1/admins/dashboard/stats returns 200 for authenticated admin."""

    mock_stats = AdminDashboardStats(total_tenants=10, active_tenants=8)

    with (
        patch(
            "api.v1.admin_dashboard_route.get_admin_dashboard_stats",
            new_callable=AsyncMock,
            return_value=mock_stats,
        ),
        patch(
            "api.v1.admin_dashboard_route.check_admin_account_status_and_permissions",
            return_value=MagicMock(id="admin1"),
        ),
    ):
        from main import app
        from httpx import AsyncClient, ASGITransport

        # Override dependency
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MagicMock(id="admin1")
        )

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/v1/admins/dashboard/stats")
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert data["data"]["totalTenants"] == 10
        finally:
            app.dependency_overrides.clear()
