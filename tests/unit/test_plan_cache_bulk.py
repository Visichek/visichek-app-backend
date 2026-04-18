"""Unit tests for ``resolve_tenant_plans_bulk``."""

from __future__ import annotations

import json
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


async def test_bulk_resolver_returns_empty_for_no_tenants() -> None:
    from services.plan_cache_service import resolve_tenant_plans_bulk

    assert await resolve_tenant_plans_bulk([]) == {}


async def test_bulk_resolver_serves_every_tenant_from_cache_in_one_mget() -> None:
    """Hot path — no Mongo work should happen at all."""
    from services import plan_cache_service

    tenant_ids = ["t_a", "t_b", "t_c"]
    cached = {
        "tenant_plan:t_a": json.dumps({"plan_id": "p1", "tenant_id": "t_a"}),
        "tenant_plan:t_b": json.dumps({"plan_id": "p1", "tenant_id": "t_b"}),
        "tenant_plan:t_c": json.dumps({"plan_id": "p2", "tenant_id": "t_c"}),
    }

    fake_redis = MagicMock()
    fake_redis.mget = MagicMock(
        return_value=[cached[f"tenant_plan:{tid}"] for tid in tenant_ids]
    )

    # If Mongo is touched at all this test fails hard.
    fake_db: Any = {"subscriptions": MagicMock(), "plans": MagicMock()}

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis),
        patch("core.database.db", fake_db),
    ):
        result = await plan_cache_service.resolve_tenant_plans_bulk(tenant_ids)

    assert set(result.keys()) == set(tenant_ids)
    assert result["t_a"]["plan_id"] == "p1"
    fake_redis.mget.assert_called_once()
    fake_db["subscriptions"].find.assert_not_called()
    fake_db["plans"].find.assert_not_called()


async def test_bulk_resolver_bulk_fetches_misses() -> None:
    from bson import ObjectId

    from services import plan_cache_service

    tenant_ids = ["t_a", "t_b"]
    plan_oid = ObjectId()
    sub_docs = [
        {
            "_id": ObjectId(),
            "tenant_id": "t_a",
            "plan_id": str(plan_oid),
            "status": "active",
            "effective_price": 100.0,
            "billing_cycle": "monthly",
            "current_period_end": 1234,
            "trial_ends_at": None,
        },
        {
            "_id": ObjectId(),
            "tenant_id": "t_b",
            "plan_id": str(plan_oid),
            "status": "trialing",
            "effective_price": 0.0,
            "billing_cycle": "monthly",
            "current_period_end": 5678,
            "trial_ends_at": 9000,
        },
    ]
    plan_doc = {
        "_id": plan_oid,
        "name": "pro",
        "display_name": "Pro",
        "tier": "professional",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "priority_support": False,
        "custom_branding": False,
        "api_access": False,
    }

    fake_redis = MagicMock()
    fake_redis.mget = MagicMock(return_value=[None, None])  # both miss
    pipe = MagicMock()
    pipe.setex = MagicMock()
    pipe.execute = MagicMock()
    fake_redis.pipeline = MagicMock(return_value=pipe)

    fake_db = {
        "subscriptions": _fake_collection(sub_docs),
        "plans": _fake_collection([plan_doc]),
    }

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis),
        patch("core.database.db", fake_db),
        patch.object(plan_cache_service, "asyncio", new=__import__("asyncio")),
    ):
        result = await plan_cache_service.resolve_tenant_plans_bulk(tenant_ids)

    assert set(result.keys()) == {"t_a", "t_b"}
    assert result["t_a"]["plan_display_name"] == "Pro"
    assert result["t_b"]["subscription_status"] == "trialing"

    # Exactly one roundtrip of each kind.
    fake_redis.mget.assert_called_once()
    assert fake_db["subscriptions"].find.call_count == 1
    assert fake_db["plans"].find.call_count == 1

    # Misses were repopulated via a single pipeline execute.
    assert pipe.setex.call_count == 2
    assert pipe.execute.call_count == 1


async def test_tenant_service_uses_bulk_resolver(monkeypatch) -> None:
    """List endpoint calls the bulk resolver exactly once, not once per tenant."""
    from services import tenant_service

    class _Tenant:
        def __init__(self, tid: str) -> None:
            self.id = tid

        def model_dump(self, by_alias: bool = False) -> dict:
            return {"id": self.id, "company_name": f"Co {self.id}"}

    fake_tenants = [_Tenant(f"t{i}") for i in range(5)]

    bulk_mock = AsyncMock(
        return_value={
            f"t{i}": {
                "plan_id": "p1",
                "plan_name": "pro",
                "plan_display_name": "Pro",
                "tier": "professional",
                "subscription_id": "s1",
                "subscription_status": "active",
            }
            for i in range(5)
        }
    )
    with (
        patch.object(
            tenant_service, "get_tenants", new=AsyncMock(return_value=fake_tenants)
        ),
        patch.object(tenant_service, "TenantWithSummaryOut", new=dict),
        patch("services.plan_cache_service.resolve_tenant_plans_bulk", new=bulk_mock),
    ):
        result = await tenant_service.retrieve_tenants_with_summary(start=0, stop=5)

    assert len(result) == 5
    bulk_mock.assert_awaited_once()
    # Single bulk call, not 5 individual ones.
    assert bulk_mock.await_count == 1
