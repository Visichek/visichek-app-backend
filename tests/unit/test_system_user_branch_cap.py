from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
class TestSystemUserBranchCap:
    async def test_seat_cap_counts_only_the_target_branches(self):
        from services.system_user_service import _enforce_seat_cap

        counter = AsyncMock(return_value=0)
        with patch("services.system_user_service.count_system_users", counter), patch(
            "services.system_user_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.system_user_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            await _enforce_seat_cap("t1", ["b1"])

        assert counter.await_args.args[0] == {
            "tenant_id": "t1",
            "branch_ids": {"$in": ["b1"]},
        }

    async def test_falls_back_to_hq_when_user_has_no_branches(self):
        from services.system_user_service import _enforce_seat_cap

        counter = AsyncMock(return_value=0)
        with patch("services.system_user_service.count_system_users", counter), patch(
            "services.system_user_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.system_user_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            await _enforce_seat_cap("t1", [])

        assert counter.await_args.args[0]["branch_ids"] == {"$in": ["hq"]}

    async def test_multi_branch_user_is_checked_against_every_branch(self):
        """A user spanning three properties consumes a seat at each."""
        from services.system_user_service import _enforce_seat_cap

        counter = AsyncMock(return_value=0)
        with patch("services.system_user_service.count_system_users", counter), patch(
            "services.system_user_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.system_user_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            await _enforce_seat_cap("t1", ["b1", "b2", "b3"])

        assert counter.await_count == 3
