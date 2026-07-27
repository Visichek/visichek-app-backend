"""Regression test for the ``sla_response_hours`` omission in plan resolution.

``services/support_case_service.py::_resolve_sla_floor_seconds`` reads
``plan_data.get("sla_response_hours")`` off the dict returned by
``get_plan_data_safe`` / ``resolve_tenant_plan``. Both ``resolve_tenant_plan``
(single-tenant path) and ``_build_resolved_from_raw`` (bulk path, used by
``resolve_tenant_plans_bulk``) build that dict by explicitly enumerating
which ``Plan`` fields to copy — ``sla_response_hours`` was missing from both
enumerations, so the SLA floor task silently never worked in production even
though its own unit tests passed (they mocked ``get_plan_data_safe`` directly
with a hand-built dict that already contained the key).

These tests exercise the *real* resolution path — real ``resolve_tenant_plan``
/ ``resolve_tenant_plans_bulk`` bodies, with only the repository/DB/Redis
boundary mocked — so a re-introduction of the omission on either path fails
here rather than silently reappearing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class _AsyncCursor:
    """Minimal async iterator stub for Motor-style ``find``."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


def _fake_collection(docs: list[dict]):
    col = MagicMock()
    col.find = MagicMock(return_value=_AsyncCursor(docs))
    return col


async def test_resolve_tenant_plan_single_path_carries_sla_response_hours() -> None:
    """The single-tenant resolver (``resolve_tenant_plan``) must surface the
    plan's ``sla_response_hours`` in the resolved snapshot, not drop it."""
    from services import plan_cache_service

    class _SubObj:
        id = "sub1"
        plan_id = "507f1f77bcf86cd799439011"
        status = "active"
        feature_overrides = None
        crud_limit_overrides = None
        retrieval_quota_overrides = None
        tenant_cap_overrides = None
        support_tier_override = None
        effective_price = 100.0
        billing_cycle = "monthly"
        current_period_end = 1234
        trial_ends_at = None

    class _PlanObj:
        name = "enterprise"
        display_name = "Enterprise"
        tier = "enterprise"
        priority_support = True
        sla_response_hours = 4  # Enterprise entitlement per config/plan_tiers.py
        custom_branding = True
        api_access = True
        support_tier = None

        def model_dump(self, mode: str = "json") -> dict:
            return {
                "feature_rules": [],
                "crud_limits": [],
                "retrieval_quotas": [],
                "storage_limits": {},
                "tenant_caps": {},
            }

    fake_redis = MagicMock()
    fake_redis.get = MagicMock(return_value=None)  # cache miss
    fake_redis.setex = MagicMock()

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis),
        patch(
            "repositories.subscription_repo.get_subscription",
            new=AsyncMock(return_value=_SubObj()),
        ),
        patch(
            "repositories.plan_repo.get_plan", new=AsyncMock(return_value=_PlanObj())
        ),
        patch(
            "repositories.tenant_addon_repo.list_active_for_tenant",
            new=AsyncMock(return_value=[]),
        ),
    ):
        snapshot = await plan_cache_service.resolve_tenant_plan("t_enterprise")

    assert snapshot is not None
    assert snapshot["sla_response_hours"] == 4


async def test_resolve_tenant_plans_bulk_carries_sla_response_hours() -> None:
    """The bulk resolver (``_build_resolved_from_raw`` via
    ``resolve_tenant_plans_bulk``) must surface ``sla_response_hours`` too —
    it has its own field-enumeration and can drift independently of the
    single-tenant path."""
    from bson import ObjectId

    from services import plan_cache_service

    plan_oid = ObjectId()
    tenant_id = "t_premium"
    sub_doc = {
        "_id": ObjectId(),
        "tenant_id": tenant_id,
        "plan_id": str(plan_oid),
        "status": "active",
        "effective_price": 50.0,
        "billing_cycle": "monthly",
        "current_period_end": 1234,
        "trial_ends_at": None,
    }
    plan_doc = {
        "_id": plan_oid,
        "name": "premium",
        "display_name": "Premium",
        "tier": "premium",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "priority_support": True,
        "sla_response_hours": 24,  # Premium entitlement per config/plan_tiers.py
        "custom_branding": False,
        "api_access": False,
    }

    fake_redis = MagicMock()
    fake_redis.mget = MagicMock(return_value=[None])  # cache miss
    pipe = MagicMock()
    pipe.setex = MagicMock()
    pipe.execute = MagicMock()
    fake_redis.pipeline = MagicMock(return_value=pipe)

    fake_db: Any = {
        "subscriptions": _fake_collection([sub_doc]),
        "plans": _fake_collection([plan_doc]),
    }

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis),
        patch("core.database.db", fake_db),
        patch.object(plan_cache_service, "asyncio", new=__import__("asyncio")),
        patch(
            "repositories.tenant_addon_repo.list_active_for_tenant",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await plan_cache_service.resolve_tenant_plans_bulk([tenant_id])

    assert result[tenant_id]["sla_response_hours"] == 24


async def test_support_case_floor_uses_real_resolution_path_end_to_end() -> None:
    """Pins the full chain: real ``resolve_tenant_plan`` -> ``get_plan_data_safe``
    -> ``_resolve_sla_floor_seconds``. Only the repo/DB/Redis boundary is
    mocked, so this fails if either the plan-cache field enumeration or the
    support-case-service key lookup regresses."""
    from services import plan_cache_service
    from services.support_case_service import _resolve_sla_floor_seconds

    class _SubObj:
        id = "sub1"
        plan_id = "507f1f77bcf86cd799439011"
        status = "active"
        feature_overrides = None
        crud_limit_overrides = None
        retrieval_quota_overrides = None
        tenant_cap_overrides = None
        support_tier_override = None
        effective_price = 100.0
        billing_cycle = "monthly"
        current_period_end = 1234
        trial_ends_at = None

    class _PlanObj:
        name = "enterprise"
        display_name = "Enterprise"
        tier = "enterprise"
        priority_support = True
        sla_response_hours = 4
        custom_branding = True
        api_access = True
        support_tier = None

        def model_dump(self, mode: str = "json") -> dict:
            return {
                "feature_rules": [],
                "crud_limits": [],
                "retrieval_quotas": [],
                "storage_limits": {},
                "tenant_caps": {},
            }

    fake_redis = MagicMock()
    fake_redis.get = MagicMock(return_value=None)
    fake_redis.setex = MagicMock()

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis),
        patch(
            "repositories.subscription_repo.get_subscription",
            new=AsyncMock(return_value=_SubObj()),
        ),
        patch(
            "repositories.plan_repo.get_plan", new=AsyncMock(return_value=_PlanObj())
        ),
        patch(
            "repositories.tenant_addon_repo.list_active_for_tenant",
            new=AsyncMock(return_value=[]),
        ),
    ):
        floor_seconds = await _resolve_sla_floor_seconds("t_enterprise")

    assert floor_seconds == 4 * 3600
