"""``supportTier`` on the admin support-case list is a join, not a field.

``support_tier`` is resolved per-request from the tenant's plan
(``plan.support_tier``, with ``subscription.support_tier_override`` winning)
and is NEVER written to a support_case document. The old spec filtered the
collection on ``support_tier`` directly, so every `?supportTier=` query
returned zero rows. These tests pin the replacement join.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.support_case_service import resolve_support_tier_tenant_filter


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d

        return gen()


def _fake_db(plans, subs):
    plans_coll = MagicMock()
    plans_coll.find = MagicMock(return_value=_Cursor(plans))
    subs_coll = MagicMock()
    subs_coll.find = MagicMock(return_value=_Cursor(subs))
    fake = MagicMock()
    fake.plans = plans_coll
    fake.subscriptions = subs_coll
    return fake


PLANS = [
    {"_id": "p_free", "support_tier": "none"},
    {"_id": "p_pro", "support_tier": "standard"},
    {"_id": "p_ent", "support_tier": "priority"},
]

SUBS = [
    {"tenant_id": "t_free", "plan_id": "p_free"},
    {"tenant_id": "t_pro", "plan_id": "p_pro"},
    {"tenant_id": "t_ent", "plan_id": "p_ent"},
    # Subscription-level override beats the plan's declared tier.
    {"tenant_id": "t_override", "plan_id": "p_free", "support_tier_override": "priority"},
]


@pytest.mark.unit
@pytest.mark.asyncio
class TestResolveSupportTierTenantFilter:
    async def test_no_tiers_requested_returns_none(self):
        with patch("services.support_case_service.db", _fake_db(PLANS, SUBS)):
            assert await resolve_support_tier_tenant_filter([]) is None

    async def test_single_tier_matches_plan_declared_tenants(self):
        with patch("services.support_case_service.db", _fake_db(PLANS, SUBS)):
            result = await resolve_support_tier_tenant_filter(["standard"])
        assert result == {"tenant_id": {"$in": ["t_pro"]}}

    async def test_subscription_override_wins_over_plan_tier(self):
        """t_override is on the free plan but overridden to priority."""
        with patch("services.support_case_service.db", _fake_db(PLANS, SUBS)):
            result = await resolve_support_tier_tenant_filter(["priority"])
        assert result is not None
        assert set(result["tenant_id"]["$in"]) == {"t_ent", "t_override"}

    async def test_multiple_tiers_union(self):
        with patch("services.support_case_service.db", _fake_db(PLANS, SUBS)):
            result = await resolve_support_tier_tenant_filter(["standard", "priority"])
        assert result is not None
        assert set(result["tenant_id"]["$in"]) == {"t_pro", "t_ent", "t_override"}

    async def test_none_tier_also_matches_tenants_with_no_subscription(self):
        """A tenant with no ACTIVE/TRIALING sub resolves to tier "none" too.

        Those tenants have no row in `subscriptions` at all, so they can only
        be matched by exclusion — an $in of explicitly-tiered tenants would
        silently drop every unsubscribed tenant's cases.
        """
        with patch("services.support_case_service.db", _fake_db(PLANS, SUBS)):
            result = await resolve_support_tier_tenant_filter(["none"])
        assert result is not None
        clauses = result["$or"]
        assert {"tenant_id": {"$in": ["t_free"]}} in clauses
        nin = next(c for c in clauses if "$nin" in c["tenant_id"])["tenant_id"]["$nin"]
        assert set(nin) == {"t_free", "t_pro", "t_ent", "t_override"}

    async def test_tier_with_no_matching_tenants_yields_empty_in_not_everything(self):
        """An unmatched tier must return nothing, never fall through to all."""
        with patch("services.support_case_service.db", _fake_db(PLANS, [])):
            result = await resolve_support_tier_tenant_filter(["priority"])
        assert result == {"tenant_id": {"$in": []}}
