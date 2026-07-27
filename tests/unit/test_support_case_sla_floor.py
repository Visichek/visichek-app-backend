from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from schemas.imports import SupportCasePriority, SupportTier
from services import support_case_service
from services.support_case_service import (
    DEFAULT_SLA_FLOOR_SECONDS,
    _compute_sla_due_at,
    _resolve_sla_floor_seconds,
)


@pytest.mark.unit
@pytest.mark.asyncio
class TestResolveSlaFloor:
    async def test_uses_plan_sla_response_hours(self):
        with patch(
            "services.support_case_service.get_plan_data_safe",
            AsyncMock(return_value={"sla_response_hours": 24}),
        ):
            assert await _resolve_sla_floor_seconds("t1") == 24 * 3600

    async def test_falls_back_to_default_when_plan_has_no_sla(self):
        with patch(
            "services.support_case_service.get_plan_data_safe",
            AsyncMock(return_value={"sla_response_hours": None}),
        ):
            assert await _resolve_sla_floor_seconds("t1") == DEFAULT_SLA_FLOOR_SECONDS

    async def test_falls_back_when_plan_unresolved(self):
        with patch(
            "services.support_case_service.get_plan_data_safe",
            AsyncMock(return_value=None),
        ):
            assert await _resolve_sla_floor_seconds("t1") == DEFAULT_SLA_FLOOR_SECONDS


@pytest.mark.unit
class TestComputeSlaDueAt:
    def test_plan_floor_widens_a_self_selected_critical(self):
        """A tenant picking CRITICAL must not buy themselves a 4h clock
        their plan does not entitle them to."""
        due = _compute_sla_due_at(SupportCasePriority.CRITICAL, 1000, 24 * 3600)
        assert due == 1000 + 24 * 3600

    def test_priority_window_wins_when_it_is_the_looser_one(self):
        due = _compute_sla_due_at(SupportCasePriority.LOW, 1000, 24 * 3600)
        assert due == 1000 + 7 * 24 * 3600

    def test_enterprise_floor_permits_a_four_hour_clock(self):
        due = _compute_sla_due_at(SupportCasePriority.CRITICAL, 1000, 4 * 3600)
        assert due == 1000 + 4 * 3600


@pytest.mark.unit
@pytest.mark.asyncio
class TestAddSupportCaseWiresTheFloor:
    """Pins the create-path wiring itself, not just the two helper functions.

    ``add_support_case`` must pass a plan-floored ``sla_due_at`` into the
    ``SupportCaseCreate`` payload it hands to ``create_support_case`` — the
    schema's own ``_compute_sla`` validator only fires when ``sla_due_at`` is
    ``None``, so if the service ever stops passing it explicitly, this test
    fails while the two-function unit tests above would keep passing.
    """

    async def test_created_case_sla_due_at_reflects_the_plan_floor(self):
        captured = {}

        async def fake_create(payload, preassigned_id=None):
            captured["payload"] = payload
            from unittest.mock import MagicMock

            fake_case = MagicMock()
            fake_case.id = "c1"
            fake_case.tenant_id = "t1"
            fake_case.subject = payload.subject
            fake_case.status = payload.status
            fake_case.priority = payload.priority
            fake_case.category = payload.category
            fake_case.opened_by = payload.opened_by
            fake_case.sla_due_at = payload.sla_due_at
            return fake_case

        with (
            patch(
                "services.support_case_service.count_open_cases_for_tenant",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "services.support_case_service.create_support_case",
                new=fake_create,
            ),
            patch(
                "services.support_case_service.get_plan_data_safe",
                new_callable=AsyncMock,
                return_value={"sla_response_hours": 24},  # Premium floor
            ),
            patch(
                "services.support_case_service._resolve_support_tier",
                new_callable=AsyncMock,
                return_value=SupportTier.NONE,
            ),
            patch(
                "services.support_case_service._resolve_tenant_company_name",
                new_callable=AsyncMock,
                return_value="Acme",
            ),
            patch(
                "services.support_case_service._resolve_tenant_opener_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.support_case_service._list_admin_recipients",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "services.support_case_service.record_audit_event",
                new_callable=AsyncMock,
            ),
            patch(
                "services.support_case_service._enqueue_list_refresh",
                new=lambda _tid: None,
            ),
            patch(
                "services.notification_service.notify_support_case_opened",
                new_callable=AsyncMock,
            ),
        ):
            # Tenant self-selects CRITICAL (raw window 4h); the mocked plan
            # only entitles them to a 24h Premium floor.
            await support_case_service.add_support_case(
                subject="Subject goes here",
                description="A description long enough to pass the guard.",
                category="other",
                priority="critical",
                tenant_id="t1",
                opened_by="u1",
                opened_by_role="super_admin",
            )

        payload = captured["payload"]
        assert payload.sla_due_at == payload.date_created + 24 * 3600
