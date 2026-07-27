"""Unit tests for addon-aware plan resolution (WS0.1).

Covers ``_apply_addon_benefits`` directly plus single/bulk resolver parity,
None-unlimited branch cap preservation, expired-addon exclusion, and the
addon activate/cancel/expire cache-invalidation wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.addon_schema import AddonKind, TenantAddonOut, TenantAddonStatus

pytestmark = pytest.mark.asyncio


def _addon_row(
    *,
    tenant_id: str = "t1",
    kind: AddonKind = AddonKind.BRANCH_QUOTA,
    quantity: int = 1,
    benefit: dict | None = None,
    status: TenantAddonStatus = TenantAddonStatus.ACTIVE,
    expires_at: int | None = None,
) -> TenantAddonOut:
    return TenantAddonOut(
        _id="507f1f77bcf86cd799439011",
        tenant_id=tenant_id,
        addon_id="addon1",
        addon_kind=kind,
        quantity=quantity,
        unit_price_snapshot=10.0,
        currency_snapshot="NGN",
        benefit_snapshot=benefit or {},
        status=status,
        expires_at=expires_at,
    )


# ─── _apply_addon_benefits ──────────────────────────────────────────


async def test_branch_quota_adds_to_finite_cap() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [
        _addon_row(kind=AddonKind.BRANCH_QUOTA, quantity=3, benefit={"branches": 2})
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 5}}
        )

    assert snapshot["tenant_caps"]["max_branches"] == 5 + 3 * 2


async def test_branch_quota_missing_benefit_key_defaults_to_one() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [_addon_row(kind=AddonKind.BRANCH_QUOTA, quantity=2, benefit={})]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 1}}
        )

    # missing "branches" key defaults to 1 per addon per the benefit_snapshot convention
    assert snapshot["tenant_caps"]["max_branches"] == 1 + 2 * 1


async def test_none_unlimited_branch_cap_preserved() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [
        _addon_row(kind=AddonKind.BRANCH_QUOTA, quantity=5, benefit={"branches": 1})
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": None}}
        )

    assert snapshot["tenant_caps"]["max_branches"] is None


async def test_visitor_quota_sets_extra_visitors_field_separately() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [
        _addon_row(kind=AddonKind.VISITOR_QUOTA, quantity=2, benefit={"visitors": 500}),
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_visitors_per_month": 1000}}
        )

    assert snapshot["extra_visitors_per_month"] == 1000
    # max_visitors_per_month itself is untouched — enforcement adds them.
    assert snapshot["tenant_caps"]["max_visitors_per_month"] == 1000


async def test_visitor_quota_missing_benefit_key_defaults_to_zero() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [_addon_row(kind=AddonKind.VISITOR_QUOTA, quantity=3, benefit={})]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits("t1", {"tenant_caps": {}})

    assert snapshot["extra_visitors_per_month"] == 0


async def test_storage_extension_addon_not_folded_in() -> None:
    """storage_extension is consumed elsewhere (storage_quota_service) — must not double-count."""
    from services.plan_cache_service import _apply_addon_benefits

    rows = [
        _addon_row(
            kind=AddonKind.STORAGE_EXTENSION, quantity=1, benefit={"storage_mb": 1024}
        )
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 5, "max_visitors_per_month": 100}}
        )

    assert snapshot["tenant_caps"]["max_branches"] == 5
    assert snapshot["extra_visitors_per_month"] == 0
    # It still shows up in the active_addons summary though.
    assert snapshot["active_addons"] == [
        {"kind": "storage_extension", "quantity": 1, "expires_at": None}
    ]


async def test_snapshot_includes_active_addons_summary() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    rows = [
        _addon_row(
            kind=AddonKind.BRANCH_QUOTA,
            quantity=2,
            benefit={"branches": 1},
            expires_at=1234567890,
        ),
        _addon_row(
            kind=AddonKind.VISITOR_QUOTA,
            quantity=1,
            benefit={"visitors": 500},
            expires_at=None,
        ),
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 1}}
        )

    assert snapshot["active_addons"] == [
        {"kind": "branch_quota", "quantity": 2, "expires_at": 1234567890},
        {"kind": "visitor_quota", "quantity": 1, "expires_at": None},
    ]


async def test_expired_addon_excluded_via_repo_filter() -> None:
    """The repo's read-time expiry filter already excludes expired rows —
    the resolver trusts that; this covers the defensive status re-check.
    """
    from services.plan_cache_service import _apply_addon_benefits

    # Even if a non-active row slips through the repo filter somehow,
    # _apply_addon_benefits defensively skips it.
    rows = [
        _addon_row(
            kind=AddonKind.BRANCH_QUOTA,
            quantity=5,
            benefit={"branches": 1},
            status=TenantAddonStatus.EXPIRED,
        )
    ]
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=rows),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 1}}
        )

    assert snapshot["tenant_caps"]["max_branches"] == 1
    assert snapshot["active_addons"] == []


async def test_addon_lookup_failure_fails_open() -> None:
    from services.plan_cache_service import _apply_addon_benefits

    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        snapshot = await _apply_addon_benefits(
            "t1", {"tenant_caps": {"max_branches": 5}}
        )

    assert snapshot["tenant_caps"]["max_branches"] == 5
    assert snapshot["active_addons"] == []
    assert snapshot["extra_visitors_per_month"] == 0


# ─── single vs bulk resolver parity ─────────────────────────────────


async def test_single_and_bulk_resolvers_produce_same_addon_inclusive_snapshot() -> (
    None
):
    """resolve_tenant_plan (single) and resolve_tenant_plans_bulk (bulk) must
    converge on the same addon-folded shape for the same tenant."""
    from bson import ObjectId

    from services import plan_cache_service

    plan_oid = ObjectId()
    tenant_id = "t_parity"

    addon_rows = [
        _addon_row(
            tenant_id=tenant_id,
            kind=AddonKind.BRANCH_QUOTA,
            quantity=2,
            benefit={"branches": 1},
        )
    ]

    # --- bulk path ---
    sub_doc = {
        "_id": ObjectId(),
        "tenant_id": tenant_id,
        "plan_id": str(plan_oid),
        "status": "active",
        "effective_price": 100.0,
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
        "tenant_caps": {"max_branches": 3},
        "priority_support": False,
        "custom_branding": False,
        "api_access": False,
    }

    class _AsyncCursor:
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

    fake_redis = MagicMock()
    fake_redis.mget = MagicMock(return_value=[None])
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
            new=AsyncMock(return_value=addon_rows),
        ),
    ):
        bulk_result = await plan_cache_service.resolve_tenant_plans_bulk([tenant_id])

    bulk_snapshot = bulk_result[tenant_id]
    assert bulk_snapshot["tenant_caps"]["max_branches"] == 3 + 2
    assert bulk_snapshot["active_addons"] == [
        {"kind": "branch_quota", "quantity": 2, "expires_at": None}
    ]

    # --- single path (same inputs, independent mocks) ---
    class _SubObj:
        id = str(sub_doc["_id"])
        plan_id = str(plan_oid)
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
        name = "premium"
        display_name = "Premium"
        tier = "premium"
        priority_support = False
        sla_response_hours = None
        custom_branding = False
        api_access = False
        support_tier = None

        def model_dump(self, mode: str = "json") -> dict:
            return {
                "feature_rules": [],
                "crud_limits": [],
                "retrieval_quotas": [],
                "storage_limits": {},
                "tenant_caps": {"max_branches": 3},
            }

    fake_redis_single = MagicMock()
    fake_redis_single.get = MagicMock(return_value=None)
    fake_redis_single.setex = MagicMock()

    with (
        patch.object(plan_cache_service, "cache_db", fake_redis_single),
        patch(
            "repositories.subscription_repo.get_subscription",
            new=AsyncMock(return_value=_SubObj()),
        ),
        patch(
            "repositories.plan_repo.get_plan", new=AsyncMock(return_value=_PlanObj())
        ),
        patch(
            "repositories.tenant_addon_repo.list_active_for_tenant",
            new=AsyncMock(return_value=addon_rows),
        ),
    ):
        single_snapshot = await plan_cache_service.resolve_tenant_plan(tenant_id)

    assert single_snapshot is not None
    assert (
        single_snapshot["tenant_caps"]["max_branches"]
        == bulk_snapshot["tenant_caps"]["max_branches"]
    )
    assert single_snapshot["active_addons"] == bulk_snapshot["active_addons"]
    assert (
        single_snapshot["extra_visitors_per_month"]
        == bulk_snapshot["extra_visitors_per_month"]
    )


# ─── invalidation wiring ─────────────────────────────────────────────


async def test_activate_addon_invalidates_plan_and_usage_caches() -> None:
    from schemas.addon_schema import AddonOut

    row = _addon_row(status=TenantAddonStatus.PENDING)
    row.payment_reference = "addon_ref1"
    updated_row = _addon_row(status=TenantAddonStatus.ACTIVE)

    fake_addon = AddonOut(
        _id="addon1",
        name="Extra branch",
        kind=AddonKind.BRANCH_QUOTA,
        unit_price=10.0,
        currency="NGN",
        validity_days=30,
    )

    with (
        patch(
            "services.addon_service.get_tenant_addon_by_reference",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "services.addon_service.get_addon_by_id",
            new=AsyncMock(return_value=fake_addon),
        ),
        patch(
            "services.addon_service.update_tenant_addon",
            new=AsyncMock(return_value=updated_row),
        ),
        patch("services.audit_service.record_audit_event", new=AsyncMock()),
        patch(
            "services.plan_cache_service.invalidate_tenant_plan_cache", new=AsyncMock()
        ) as mock_invalidate_plan,
        patch("core.queue.precompute.delete_precompute") as mock_delete_precompute,
        patch("core.queue.entity_cache.invalidate_entity") as mock_invalidate_entity,
    ):
        from services.addon_service import activate_tenant_addon_by_reference

        await activate_tenant_addon_by_reference(payment_reference="addon_ref1")

    mock_invalidate_plan.assert_awaited_once_with(row.tenant_id)
    mock_delete_precompute.assert_called_once_with(
        "usage.my_usage", tenant_id=row.tenant_id
    )
    mock_invalidate_entity.assert_called_once_with("tenant_usage", row.tenant_id)


async def test_cancel_addon_invalidates_plan_and_usage_caches() -> None:
    row = _addon_row(status=TenantAddonStatus.ACTIVE)
    cancelled_row = _addon_row(status=TenantAddonStatus.CANCELLED)

    with (
        patch(
            "services.addon_service.get_tenant_addon_by_id",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "services.addon_service.update_tenant_addon",
            new=AsyncMock(return_value=cancelled_row),
        ),
        patch("services.audit_service.record_audit_event", new=AsyncMock()),
        patch(
            "services.plan_cache_service.invalidate_tenant_plan_cache", new=AsyncMock()
        ) as mock_invalidate_plan,
        patch("core.queue.precompute.delete_precompute") as mock_delete_precompute,
        patch("core.queue.entity_cache.invalidate_entity") as mock_invalidate_entity,
    ):
        from services.addon_service import cancel_tenant_addon

        await cancel_tenant_addon("tenant_addon_1", actor_id="admin1")

    mock_invalidate_plan.assert_awaited_once_with(row.tenant_id)
    mock_delete_precompute.assert_called_once_with(
        "usage.my_usage", tenant_id=row.tenant_id
    )
    mock_invalidate_entity.assert_called_once_with("tenant_usage", row.tenant_id)


async def test_expire_sweep_invalidates_caches_for_each_affected_tenant() -> None:
    with (
        patch(
            "repositories.tenant_addon_repo.list_due_tenant_ids_for_expiry",
            new=AsyncMock(return_value=["t1", "t2"]),
        ),
        patch(
            "repositories.tenant_addon_repo.expire_due_tenant_addons",
            new=AsyncMock(return_value=2),
        ),
        patch(
            "services.plan_cache_service.invalidate_tenant_plan_cache", new=AsyncMock()
        ) as mock_invalidate_plan,
        patch("core.queue.precompute.delete_precompute") as mock_delete_precompute,
        patch("core.queue.entity_cache.invalidate_entity") as mock_invalidate_entity,
    ):
        from services.addon_service import expire_due_addons

        count = await expire_due_addons()

    assert count == 2
    assert mock_invalidate_plan.await_count == 2
    assert mock_delete_precompute.call_count == 2
    assert mock_invalidate_entity.call_count == 2
    mock_invalidate_plan.assert_any_await("t1")
    mock_invalidate_plan.assert_any_await("t2")


# ─── me_limitations_service ──────────────────────────────────────────


async def test_me_limitations_includes_active_addons_summary() -> None:
    from security.principal import AuthPrincipal

    plan_data = {
        "plan_id": "p1",
        "plan_name": "premium",
        "tier": "premium",
        "subscription_status": "active",
        "tenant_caps": {"max_branches": 5},
        "feature_rules": [],
        "active_addons": [
            {"kind": "branch_quota", "quantity": 2, "expires_at": None},
        ],
    }
    principal = AuthPrincipal(
        user_id="u1",
        role="super_admin",
        access_token_id="tok1",
        jwt_token="jwt",
        tenant_id="t1",
    )

    with (
        patch(
            "services.me_limitations_service.resolve_tenant_plan",
            new=AsyncMock(return_value=plan_data),
        ),
        patch(
            "services.me_limitations_service._list_locked_branch_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.me_limitations_service._list_locked_department_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.me_limitations_service._build_plan_block",
            new=AsyncMock(return_value={"id": "p1"}),
        ),
    ):
        from services.me_limitations_service import build_me_limitations

        result = await build_me_limitations(principal)

    assert result["activeAddons"] == [
        {"kind": "branch_quota", "quantity": 2, "expires_at": None}
    ]
