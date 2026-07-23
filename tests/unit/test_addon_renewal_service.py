"""Unit tests for Task 8: add-on renewal + expiry branch-locking.

Covers: renewal success rolls expiry + reprices from live Premium;
failed charge -> grace -> expired (with cache invalidation + branch
locking); no-saved-instrument is treated as a failure (never a fake
success); cancel/expiry trigger the branch lock walk; locked branches
excluded from usage count but still counted by _enforce_branch_cap;
the branch status list-spec filter bug fix.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.addon_schema import AddonKind, AddonOut, AddonStatus, TenantAddonOut, TenantAddonStatus, TenantAddonUpdate

pytestmark = pytest.mark.asyncio


def _row(**overrides: Any) -> TenantAddonOut:
    now = int(time.time())
    defaults: dict[str, Any] = {
        "_id": "ta1",
        "tenant_id": "507f1f77bcf86cd799439011",
        "addon_id": "addon1",
        "addon_kind": AddonKind.BRANCH_QUOTA,
        "quantity": 1,
        "unit_price_snapshot": 120_000.0,
        "currency_snapshot": "NGN",
        "recurring_snapshot": True,
        "billing_cycle_snapshot": "monthly",
        "status": TenantAddonStatus.ACTIVE,
        "expires_at": now - 10,
        "renewal_attempts": 0,
        "date_created": now,
    }
    defaults.update(overrides)
    return TenantAddonOut(**defaults)


def _addon(**overrides: Any) -> AddonOut:
    defaults: dict[str, Any] = {
        "_id": "addon1",
        "slug": "additional-branch",
        "name": "Additional Branch",
        "kind": AddonKind.BRANCH_QUOTA,
        "status": AddonStatus.ACTIVE,
        "unit_price": 100_000.0,
        "currency": "NGN",
        "pricing_mode": "derived",
        "derived_from": {"plan": "premium", "field": "base_price_monthly", "multiplier": 0.8},
        "recurring": True,
        "billing_cycle": "monthly",
        "benefit_per_unit": {"branches": 1},
    }
    defaults.update(overrides)
    return AddonOut(**defaults)


# ─── Renewal success ──────────────────────────────────────────────────


async def test_renewal_success_rolls_expiry_and_reprices_from_live_premium() -> None:
    from core.payments.types import PaymentStatus
    from services.addon_renewal_service import renew_due_addons

    row = _row(unit_price_snapshot=100_000.0)
    now = int(time.time())
    tx = MagicMock(status=PaymentStatus.SUCCEEDED)

    mock_provider = MagicMock()
    mock_provider.charge_recurring.return_value = tx
    mock_manager = MagicMock()
    mock_manager.get_provider.return_value = mock_provider

    tenant = MagicMock(
        paystack_authorization_code="auth_123",
        paystack_auth_email="a@b.com",
    )

    with (
        patch(
            "services.addon_renewal_service.list_due_recurring_tenant_addons",
            new=AsyncMock(return_value=[row]),
        ),
        patch("services.addon_renewal_service.get_addon_by_id", new=AsyncMock(return_value=_addon())),
        patch(
            "services.addon_renewal_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=160_000.0),  # live Premium price moved
        ),
        patch("services.addon_renewal_service.PaymentManager.get_instance", return_value=mock_manager),
        patch(
            "services.addon_renewal_service._get_provider_for_tenant",
            new=AsyncMock(return_value="paystack"),
        ),
        patch("services.addon_renewal_service.get_tenant", new=AsyncMock(return_value=tenant)),
        patch(
            "services.addon_renewal_service.update_tenant_addon", new=AsyncMock(return_value=row)
        ) as mock_update,
        patch("services.addon_renewal_service.generate_invoice", new=AsyncMock()),
        patch("services.addon_renewal_service.record_audit_event", new=AsyncMock()),
        patch(
            "services.addon_renewal_service._invalidate_tenant_addon_caches", new=AsyncMock()
        ) as mock_invalidate,
    ):
        result = await renew_due_addons()

    assert result["renewed"] == 1
    assert result["expired"] == 0
    mock_update.assert_awaited_once()
    assert mock_update.await_args is not None
    payload: TenantAddonUpdate = mock_update.await_args.args[1]
    assert payload.status == TenantAddonStatus.ACTIVE
    assert payload.unit_price_snapshot == 160_000.0  # re-priced from live plan
    assert payload.expires_at is not None
    assert payload.expires_at >= now + 29 * 86400  # rolled ~one month forward
    assert payload.renewal_attempts == 0
    mock_invalidate.assert_awaited_once_with(row.tenant_id)
    mock_provider.charge_recurring.assert_called_once()


# ─── No saved instrument is a FAILURE, not a fake success ─────────────


async def test_no_saved_instrument_is_failed_renewal_not_success() -> None:
    """The core anti-regression check: create_intent is never called and
    a missing chargeable instrument must NOT flip the addon ACTIVE with a
    rolled expiry — it must go through the grace/fail path."""
    from services.addon_renewal_service import renew_due_addons

    row = _row(renewal_attempts=0)
    mock_manager = MagicMock()
    mock_provider = MagicMock()
    mock_manager.get_provider.return_value = mock_provider

    with (
        patch(
            "services.addon_renewal_service.list_due_recurring_tenant_addons",
            new=AsyncMock(return_value=[row]),
        ),
        patch("services.addon_renewal_service.get_addon_by_id", new=AsyncMock(return_value=_addon())),
        patch(
            "services.addon_renewal_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=120_000.0),
        ),
        patch("services.addon_renewal_service.PaymentManager.get_instance", return_value=mock_manager),
        patch(
            "services.addon_renewal_service._get_provider_for_tenant",
            new=AsyncMock(return_value="paystack"),
        ),
        # No tenant record -> no saved instrument at all.
        patch("services.addon_renewal_service.get_tenant", new=AsyncMock(return_value=None)),
        patch(
            "services.addon_renewal_service.update_tenant_addon", new=AsyncMock(return_value=row)
        ) as mock_update,
        patch("services.addon_renewal_service.record_audit_event", new=AsyncMock()),
        patch("services.addon_renewal_service._invalidate_tenant_addon_caches", new=AsyncMock()),
    ):
        result = await renew_due_addons()

    # Never charged (no instrument to charge) and never falls back to any
    # intent-creation "success".
    mock_provider.charge_recurring.assert_not_called()
    assert result["renewed"] == 0
    assert result["grace"] == 1
    assert mock_update.await_args is not None
    payload: TenantAddonUpdate = mock_update.await_args.args[1]
    assert payload.status is None  # status untouched -> stays ACTIVE (grace), not flipped
    assert payload.renewal_attempts == 1
    assert payload.next_retry_at is not None


# ─── Declined charge -> grace -> exhausted -> expired ──────────────────


async def test_declined_charge_enters_grace_then_expires_after_max_attempts() -> None:
    from core.payments.types import PaymentStatus
    from services.addon_renewal_service import renew_due_addons

    row = _row(renewal_attempts=5)  # already at settings.max_dunning_attempts (default 5)

    tx = MagicMock(status=PaymentStatus.FAILED)
    mock_provider = MagicMock()
    mock_provider.charge_recurring.return_value = tx
    mock_manager = MagicMock()
    mock_manager.get_provider.return_value = mock_provider
    tenant = MagicMock(paystack_authorization_code="auth_123", paystack_auth_email="a@b.com")

    with (
        patch(
            "services.addon_renewal_service.list_due_recurring_tenant_addons",
            new=AsyncMock(return_value=[row]),
        ),
        patch("services.addon_renewal_service.get_addon_by_id", new=AsyncMock(return_value=_addon())),
        patch(
            "services.addon_renewal_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=120_000.0),
        ),
        patch("services.addon_renewal_service.PaymentManager.get_instance", return_value=mock_manager),
        patch(
            "services.addon_renewal_service._get_provider_for_tenant",
            new=AsyncMock(return_value="paystack"),
        ),
        patch("services.addon_renewal_service.get_tenant", new=AsyncMock(return_value=tenant)),
        patch(
            "services.addon_renewal_service.update_tenant_addon", new=AsyncMock(return_value=row)
        ) as mock_update,
        patch("services.addon_renewal_service.record_audit_event", new=AsyncMock()),
        patch(
            "services.addon_renewal_service._invalidate_tenant_addon_caches", new=AsyncMock()
        ) as mock_invalidate,
    ):
        result = await renew_due_addons()

    assert result["expired"] == 1
    assert result["renewed"] == 0
    assert mock_update.await_args is not None
    payload: TenantAddonUpdate = mock_update.await_args.args[1]
    assert payload.status == TenantAddonStatus.EXPIRED
    assert payload.renewal_attempts == 6
    mock_invalidate.assert_awaited_once_with(row.tenant_id)


# ─── Branch lock walk ──────────────────────────────────────────────────


async def test_enforce_branch_lock_locks_newest_branches_beyond_cap() -> None:
    from services.branch_service import enforce_branch_lock

    docs = [
        {"_id": "hq", "is_headquarters": True, "status": "active"},
        {"_id": "old", "is_headquarters": False, "status": "active"},
        {"_id": "newest", "is_headquarters": False, "status": "active"},
    ]

    class _FakeCursor:
        def __init__(self, items):
            self._items = items

        def sort(self, *_args, **_kwargs):
            return self

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for d in self._items:
                yield d

    fake_collection = MagicMock()
    fake_collection.find.return_value = _FakeCursor(docs)
    update_result = MagicMock(modified_count=1)
    fake_collection.update_many = AsyncMock(return_value=update_result)
    fake_db = {"branches": fake_collection}

    with (
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tenant_caps": {"max_branches": 2}}),
        ),
        patch("core.database.db", fake_db),
    ):
        locked = await enforce_branch_lock("t1")

    assert locked == 1
    fake_collection.update_many.assert_awaited_once()
    call_args = fake_collection.update_many.await_args
    assert call_args.args[0] == {"_id": {"$in": ["newest"]}}


async def test_enforce_branch_lock_noop_when_unlimited() -> None:
    from services.branch_service import enforce_branch_lock

    with patch(
        "services.plan_cache_service.resolve_tenant_plan",
        new=AsyncMock(return_value={"tenant_caps": {"max_branches": None}}),
    ):
        locked = await enforce_branch_lock("t1")

    assert locked == 0


async def test_cancel_tenant_addon_triggers_cache_invalidation_which_locks_branches() -> None:
    """cancel_tenant_addon must invalidate through
    _invalidate_tenant_addon_caches, which runs the branch lock walk."""
    from services.addon_service import cancel_tenant_addon

    row = TenantAddonOut(
        _id="ta1",
        tenant_id="t1",
        addon_id="addon1",
        addon_kind=AddonKind.BRANCH_QUOTA,
        quantity=1,
        unit_price_snapshot=120_000.0,
        currency_snapshot="NGN",
        status=TenantAddonStatus.ACTIVE,
    )
    updated = row.model_copy(update={"status": TenantAddonStatus.CANCELLED})

    with (
        patch("services.addon_service.get_tenant_addon_by_id", new=AsyncMock(return_value=row)),
        patch("services.addon_service.update_tenant_addon", new=AsyncMock(return_value=updated)),
        patch("services.addon_service.record_audit_event", new=AsyncMock()),
        patch("services.branch_service.enforce_branch_lock", new=AsyncMock(return_value=1)) as mock_lock,
        patch("services.plan_cache_service.invalidate_tenant_plan_cache", new=AsyncMock()),
        patch("core.queue.precompute.delete_precompute"),
        patch("core.queue.entity_cache.invalidate_entity"),
    ):
        await cancel_tenant_addon("ta1", actor_id="admin1")

    mock_lock.assert_awaited_once_with("t1")


# ─── Usage count excludes locked branches; cap enforcement includes them ─


async def test_locked_branches_excluded_from_usage_count() -> None:
    """usage_service's branch count must read `status != inactive`, not
    the phantom `is_active` field."""
    import inspect

    import services.usage_service as usage_service_module

    src = inspect.getsource(usage_service_module)
    assert '{"tenant_id": tenant_id, "status": {"$ne": "inactive"}}' in src


async def test_enforce_branch_cap_counts_all_branches_including_locked() -> None:
    """_enforce_branch_cap must keep counting ALL branches (status-agnostic)
    so a locked branch never frees up cap space."""
    from services.branch_service import _enforce_branch_cap

    with (
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tenant_caps": {"max_branches": 1}}),
        ),
        patch("services.branch_service.count_branches", new=AsyncMock(return_value=1)) as mock_count,
    ):
        with pytest.raises(Exception):
            await _enforce_branch_cap("t1")

    # count_branches called with tenant_id filter only — no status filter.
    mock_count.assert_awaited_once_with({"tenant_id": "t1"})


# ─── Branch list-spec status filter fix ────────────────────────────────


def test_branches_list_spec_status_filter_uses_status_field() -> None:
    from api.v1.branch_route import BRANCHES_LIST_SPEC

    status_filter = BRANCHES_LIST_SPEC.filters["status"]
    assert status_filter.builder is not None
    assert status_filter.builder(["active"]) == {"status": "active"}
    assert status_filter.builder(["active", "inactive"]) == {
        "status": {"$in": ["active", "inactive"]}
    }
    assert status_filter.builder(["all"]) == {}
    assert "is_active" not in BRANCHES_LIST_SPEC.sortable_fields


# ─── me_limitations extraVisitorsPerMonth ──────────────────────────────


async def test_me_limitations_includes_extra_visitors_per_month() -> None:
    from services.me_limitations_service import build_me_limitations

    principal = MagicMock(tenant_id="t1")
    plan_data = {
        "plan_id": "p1",
        "plan_name": "premium",
        "tier": "premium",
        "tenant_caps": {"max_branches": 3, "max_visitors_per_month": 1000},
        "feature_rules": [],
        "extra_visitors_per_month": 2000,
        "active_addons": [],
    }
    with (
        patch("services.me_limitations_service.resolve_tenant_plan", new=AsyncMock(return_value=plan_data)),
        patch(
            "services.me_limitations_service._list_locked_branch_ids", new=AsyncMock(return_value=[])
        ),
        patch(
            "services.me_limitations_service._list_locked_department_ids",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_me_limitations(principal)

    assert result["caps"]["extraVisitorsPerMonth"] == 2000
