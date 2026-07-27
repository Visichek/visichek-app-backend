from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from schemas.imports import SupportCasePriority
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
