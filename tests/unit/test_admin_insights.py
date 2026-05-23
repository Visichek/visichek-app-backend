"""Unit tests for the platform-admin insights endpoint.

Pure structure (tab->section catalogue, filter container, the new `table`
section shape) is tested directly; the route test mocks the service so it does
not touch Mongo (and passes ?tab to skip the precompute-cache branch)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from schemas.insights_schema import (
    AdminInsightsMeta,
    AdminInsightsResponse,
    InsightsSection,
    Kpi,
)
from services.admin_insights_service import (
    _TAB_SECTIONS,
    _build_admin_applied_filters,
    _Filters,
)


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    yield
    app.dependency_overrides.clear()


class TestTabCatalogue:
    def test_all_five_tabs_present(self):
        assert set(_TAB_SECTIONS) == {
            "overview",
            "tenants",
            "billing",
            "activity",
            "risk",
        }

    def test_section_ids_are_known(self):
        known = {
            "tenantSignups",
            "revenue",
            "planTier",
            "topRevenue",
            "geography",
            "roleBreakdown",
            "recentSignups",
            "newSubscriptions",
            "invoiceStatus",
            "billingCycle",
            "paymentProvider",
            "visitorCheckIns",
            "visitorSignups",
            "hourly",
            "topVisitors",
            "topActivity",
            "onboarding",
            "incidents",
            "incidentType",
            "incidentStatus",
            "dsrStatus",
            "supportStatus",
            "supportPriority",
            "topIncidents",
            "topSupport",
        }
        for tab, ids in _TAB_SECTIONS.items():
            assert set(ids) <= known, f"{tab} references unknown section ids"


class TestFilters:
    def test_filters_capture_all_params(self):
        f = _Filters(plan_tier="premium", country="Nigeria", tenant_id="t1")
        assert f.plan_tier == "premium"
        assert f.country == "Nigeria"
        assert f.tenant_id == "t1"
        # Unset filters default to None
        assert f.incident_type is None


class TestAppliedFilters:
    @pytest.mark.asyncio
    async def test_enum_filters_humanised_with_camelcase_keys(self):
        # enum/raw filters never touch the DB
        chips = await _build_admin_applied_filters(
            {
                "subscription_status": "active",
                "plan_tier": "premium",
                "country": "Nigeria",
            }
        )
        by_key = {c.key: c.label for c in chips}
        assert by_key["subscriptionStatus"] == "Active"
        assert by_key["planTier"] == "Premium"
        assert by_key["country"] == "Nigeria"  # raw value preserved

    @pytest.mark.asyncio
    async def test_unset_filters_are_omitted(self):
        chips = await _build_admin_applied_filters({"plan_tier": None, "country": ""})
        assert chips == []


class TestTableSection:
    def test_table_section_carries_rows_and_columns(self):
        sec = InsightsSection(
            type="table",
            title="Top tenants by revenue",
            rows=[{"companyName": "Acme", "monthlyRevenue": 1000}],
            columns=["companyName", "monthlyRevenue"],
        )
        assert sec.type == "table"
        assert sec.rows[0]["companyName"] == "Acme"
        assert sec.columns == ["companyName", "monthlyRevenue"]


class TestAdminInsightsRoute:
    @pytest.mark.asyncio
    async def test_returns_envelope_with_table_section(
        self, cleanup_dependency_overrides
    ):
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MagicMock()
        )

        fake = AdminInsightsResponse(
            meta=AdminInsightsMeta(
                tab="overview",
                platform_launch_at=1,
                applied_range={"start": 1, "stop": 2},
                granularity="day",
            ),
            kpis=[Kpi(key="totalTenants", label="Total tenants", value=175)],
            sections={
                "topRevenue": InsightsSection(
                    type="table",
                    title="Top tenants by revenue",
                    rows=[{"companyName": "Acme", "monthlyRevenue": 1000}],
                    columns=["companyName", "monthlyRevenue"],
                )
            },
        )

        with patch(
            "api.v1.admin_dashboard_route.get_admin_insights",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = fake
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # ?tab forces the on-demand path (skips the precompute cache).
                resp = await client.get(
                    "/v1/admins/dashboard/insights?tab=overview",
                    headers={"Authorization": "Bearer x"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["meta"]["tab"] == "overview"
        assert data["data"]["kpis"][0]["key"] == "totalTenants"
        section = data["data"]["sections"]["topRevenue"]
        assert section["type"] == "table"
        assert section["rows"][0]["companyName"] == "Acme"
        assert section["columns"] == ["companyName", "monthlyRevenue"]
        mock_get.assert_awaited_once()
