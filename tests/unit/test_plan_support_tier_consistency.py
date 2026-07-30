from __future__ import annotations

import pytest

from config.plan_tiers import (
    ENTERPRISE_TEMPLATE,
    FREE_PLAN,
    PREMIUM_PLAN,
    STARTER_PLAN,
)
from schemas.imports import SupportTier


@pytest.mark.unit
class TestSupportTierConsistency:
    @pytest.mark.parametrize(
        "plan",
        [FREE_PLAN, STARTER_PLAN, PREMIUM_PLAN, ENTERPRISE_TEMPLATE],
        ids=lambda p: p.name,
    )
    def test_priority_support_flag_matches_support_tier(self, plan):
        """priority_support is the marketing flag; support_tier is what the
        support-case service actually branches on. They must agree or the
        catalogue advertises an entitlement the code does not grant."""
        assert plan.priority_support == (plan.support_tier == SupportTier.PRIORITY), (
            f"{plan.name}: priority_support={plan.priority_support} but "
            f"support_tier={plan.support_tier}"
        )

    def test_premium_grants_priority_support(self):
        assert PREMIUM_PLAN.support_tier == SupportTier.PRIORITY
        assert PREMIUM_PLAN.sla_response_hours == 24
