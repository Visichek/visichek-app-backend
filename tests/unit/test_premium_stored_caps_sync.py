from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.premium_max_branches_flip_migration import sync_stored_premium_caps


def _fake_db(plan_doc):
    """Fake Motor db exposing db["plans"].find_one / update_one."""
    plans = MagicMock()
    plans.find_one = AsyncMock(return_value=plan_doc)
    plans.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    fake = MagicMock()
    fake.__getitem__.return_value = plans
    return fake, plans


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncStoredPremiumCaps:
    async def test_adds_missing_visitors_per_branch_per_month(self):
        plan_doc = {
            "_id": "plan-1",
            "name": "premium",
            "tenant_caps": {"max_branches": 1, "max_visitors_per_month": 500},
        }
        fake, plans = _fake_db(plan_doc)
        with patch("services.premium_max_branches_flip_migration.db", fake), patch(
            "services.premium_max_branches_flip_migration.invalidate_plan_cache",
            AsyncMock(),
        ):
            changed = await sync_stored_premium_caps()

        assert "visitors_per_branch_per_month" in changed
        assert changed["visitors_per_branch_per_month"] == (None, 1000)
        set_payload = plans.update_one.await_args.args[1]["$set"]
        assert set_payload["tenant_caps.visitors_per_branch_per_month"] == 1000

    async def test_noop_when_already_in_sync(self):
        plan_doc = {
            "_id": "plan-1",
            "name": "premium",
            "tenant_caps": {
                "max_branches": 1,
                "visitors_per_branch_per_month": 1000,
            },
        }
        fake, plans = _fake_db(plan_doc)
        with patch("services.premium_max_branches_flip_migration.db", fake), patch(
            "services.premium_max_branches_flip_migration.invalidate_plan_cache",
            AsyncMock(),
        ):
            changed = await sync_stored_premium_caps()

        assert changed == {}
        plans.update_one.assert_not_awaited()

    async def test_noop_when_plan_doc_absent(self):
        fake, plans = _fake_db(None)
        with patch("services.premium_max_branches_flip_migration.db", fake), patch(
            "services.premium_max_branches_flip_migration.invalidate_plan_cache",
            AsyncMock(),
        ):
            changed = await sync_stored_premium_caps()

        assert changed == {}
        plans.update_one.assert_not_awaited()
