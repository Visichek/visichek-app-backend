"""Unit tests for Task 9: grandfathering backfill + Premium max_branches flip.

Covers:
- grant quantity matches (active branches - 1), zero-price + perpetual + tagged
- idempotent re-run (already-granted tenants skipped, no duplicate row)
- effective max_branches for a 3-branch Premium tenant == 3 after migration
  (1 canonical cap + 2 granted) via the plan-cache addon-benefit fold
- new Premium signup (no grant) gets max_branches == 1 (canonical config)
- notification sent once per tenant, gated by its own marker
- stored plan doc flip: migration updates existing doc, is idempotent,
  and invalidates the plan cache
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeCursor(_AsyncIter):
    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def batch_size(self, *a, **kw):
        return self


class _FakeCollection:
    def __init__(self, *, find_one_val=None, count_val=0, find_items=None):
        self.find_one_val = find_one_val
        self.count_val = count_val
        self.find_items = find_items or []
        self.updated: list[tuple[Any, Any]] = []

    async def find_one(self, filt=None, *a, **kw):
        return self.find_one_val

    async def count_documents(self, *a, **kw):
        return self.count_val

    def find(self, *a, **kw):
        return _FakeCursor(list(self.find_items))

    async def update_one(self, filt, update, *a, **kw):
        self.updated.append((filt, update))
        return AsyncMock(modified_count=1)


class _FakeDB:
    def __init__(self, collections: dict[str, _FakeCollection]):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.setdefault(name, _FakeCollection())

    def __getattr__(self, name):
        return self._collections.setdefault(name, _FakeCollection())


def _fake_admin(admin_id: str = "admin1"):
    from unittest.mock import MagicMock

    m = MagicMock()
    m.id = admin_id
    return m


# ─── Grant math + idempotency ─────────────────────────────────────────


async def test_grant_quantity_matches_active_branch_count_minus_one():
    from services.premium_branch_grandfather_backfill import (
        backfill_premium_branch_grandfathering,
    )

    plans_col = _FakeCollection(find_one_val={"_id": "plan1", "name": "premium"})
    subs_col = _FakeCollection(
        find_items=[{"tenant_id": "tenant-1", "status": "active"}]
    )
    branches_col = _FakeCollection(count_val=3)  # 3 active branches
    tenant_addons_col = _FakeCollection(find_one_val=None)  # not yet granted
    markers_col = _FakeCollection(find_one_val=None)

    fake_db = _FakeDB(
        {
            "plans": plans_col,
            "subscriptions": subs_col,
            "branches": branches_col,
            "tenant_addons": tenant_addons_col,
            "backfill_markers": markers_col,
        }
    )

    with (
        patch("services.premium_branch_grandfather_backfill.db", fake_db),
        patch(
            "services.premium_branch_grandfather_backfill.get_addon",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.create_tenant_addon",
            new=AsyncMock(),
        ) as mock_create,
        patch(
            "services.premium_branch_grandfather_backfill._invalidate_tenant_addon_caches",
            new=AsyncMock(),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.get_main_super_admin",
            new=AsyncMock(return_value=_fake_admin()),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.send_notification",
            new=AsyncMock(),
        ) as mock_notify,
    ):
        summary = await backfill_premium_branch_grandfathering()

    assert summary["grants_created"] == 1
    mock_create.assert_awaited_once()
    payload = mock_create.await_args.args[0]
    assert payload.quantity == 2  # 3 branches - 1
    assert payload.unit_price_snapshot == 0.0
    assert payload.status.value == "active"
    assert payload.recurring_snapshot is False
    assert payload.expires_at is None
    assert payload.metadata == {"granted": "premium-per-location-migration"}
    mock_notify.assert_awaited_once()


async def test_idempotent_rerun_skips_already_granted_tenant():
    from services.premium_branch_grandfather_backfill import (
        backfill_premium_branch_grandfathering,
    )

    plans_col = _FakeCollection(find_one_val={"_id": "plan1", "name": "premium"})
    subs_col = _FakeCollection(
        find_items=[{"tenant_id": "tenant-1", "status": "active"}]
    )
    branches_col = _FakeCollection(count_val=3)
    # Already has a granted row.
    tenant_addons_col = _FakeCollection(
        find_one_val={"metadata": {"granted": "premium-per-location-migration"}}
    )
    markers_col = _FakeCollection(
        find_one_val={"_id": "premium_grandfather_notified:tenant-1"}
    )

    fake_db = _FakeDB(
        {
            "plans": plans_col,
            "subscriptions": subs_col,
            "branches": branches_col,
            "tenant_addons": tenant_addons_col,
            "backfill_markers": markers_col,
        }
    )

    with (
        patch("services.premium_branch_grandfather_backfill.db", fake_db),
        patch(
            "services.premium_branch_grandfather_backfill.get_addon",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.create_tenant_addon",
            new=AsyncMock(),
        ) as mock_create,
        patch(
            "services.premium_branch_grandfather_backfill.send_notification",
            new=AsyncMock(),
        ) as mock_notify,
    ):
        summary = await backfill_premium_branch_grandfathering()

    assert summary["already_granted"] == 1
    assert summary["grants_created"] == 0
    mock_create.assert_not_awaited()
    # Already notified — must not notify again.
    mock_notify.assert_not_awaited()


async def test_single_branch_tenant_gets_no_grant_but_is_notified_once():
    from services.premium_branch_grandfather_backfill import (
        backfill_premium_branch_grandfathering,
    )

    plans_col = _FakeCollection(find_one_val={"_id": "plan1", "name": "premium"})
    subs_col = _FakeCollection(
        find_items=[{"tenant_id": "tenant-1", "status": "trialing"}]
    )
    branches_col = _FakeCollection(count_val=1)
    tenant_addons_col = _FakeCollection(find_one_val=None)
    markers_col = _FakeCollection(find_one_val=None)

    fake_db = _FakeDB(
        {
            "plans": plans_col,
            "subscriptions": subs_col,
            "branches": branches_col,
            "tenant_addons": tenant_addons_col,
            "backfill_markers": markers_col,
        }
    )

    with (
        patch("services.premium_branch_grandfather_backfill.db", fake_db),
        patch(
            "services.premium_branch_grandfather_backfill.get_addon",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.create_tenant_addon",
            new=AsyncMock(),
        ) as mock_create,
        patch(
            "services.premium_branch_grandfather_backfill.get_main_super_admin",
            new=AsyncMock(return_value=_fake_admin()),
        ),
        patch(
            "services.premium_branch_grandfather_backfill.send_notification",
            new=AsyncMock(),
        ) as mock_notify,
    ):
        summary = await backfill_premium_branch_grandfathering()

    assert summary["no_grant_needed"] == 1
    mock_create.assert_not_awaited()
    mock_notify.assert_awaited_once()


# ─── Effective max_branches after migration (plan-cache fold) ─────────


async def test_three_branch_tenant_effective_max_branches_is_three_after_grant():
    """1 (new canonical cap) + 2 (granted quantity) == 3."""
    from services.plan_cache_service import _apply_addon_benefits
    from schemas.addon_schema import AddonKind, TenantAddonOut, TenantAddonStatus

    granted_row = TenantAddonOut(
        _id="ta1",
        tenant_id="tenant-1",
        addon_id="addon1",
        addon_kind=AddonKind.BRANCH_QUOTA,
        quantity=2,
        unit_price_snapshot=0.0,
        benefit_snapshot={"branches": 1},
        status=TenantAddonStatus.ACTIVE,
        expires_at=None,
    )

    snapshot = {"tenant_caps": {"max_branches": 1}}
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=[granted_row]),
    ):
        result = await _apply_addon_benefits("tenant-1", snapshot)

    assert result["tenant_caps"]["max_branches"] == 3


async def test_new_premium_signup_with_no_addons_has_max_branches_one():
    from services.plan_cache_service import _apply_addon_benefits

    snapshot = {"tenant_caps": {"max_branches": 1}}
    with patch(
        "repositories.tenant_addon_repo.list_active_for_tenant",
        new=AsyncMock(return_value=[]),
    ):
        result = await _apply_addon_benefits("tenant-new", snapshot)

    assert result["tenant_caps"]["max_branches"] == 1


# ─── Safety check ──────────────────────────────────────────────────────


async def test_safety_check_flags_tenant_over_effective_cap():
    from services.premium_branch_grandfather_backfill import (
        check_premium_branch_cap_safety,
    )

    plans_col = _FakeCollection(find_one_val={"_id": "plan1", "name": "premium"})
    subs_col = _FakeCollection(
        find_items=[{"tenant_id": "tenant-over", "status": "active"}]
    )
    branches_col = _FakeCollection(count_val=5)
    fake_db = _FakeDB(
        {"plans": plans_col, "subscriptions": subs_col, "branches": branches_col}
    )

    with (
        patch("services.premium_branch_grandfather_backfill.db", fake_db),
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tenant_caps": {"max_branches": 3}}),
        ),
    ):
        violators = await check_premium_branch_cap_safety()

    assert violators == ["tenant-over"]


async def test_safety_check_empty_when_within_cap():
    from services.premium_branch_grandfather_backfill import (
        check_premium_branch_cap_safety,
    )

    plans_col = _FakeCollection(find_one_val={"_id": "plan1", "name": "premium"})
    subs_col = _FakeCollection(
        find_items=[{"tenant_id": "tenant-ok", "status": "active"}]
    )
    branches_col = _FakeCollection(count_val=3)
    fake_db = _FakeDB(
        {"plans": plans_col, "subscriptions": subs_col, "branches": branches_col}
    )

    with (
        patch("services.premium_branch_grandfather_backfill.db", fake_db),
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tenant_caps": {"max_branches": 3}}),
        ),
    ):
        violators = await check_premium_branch_cap_safety()

    assert violators == []


# ─── Stored plan doc flip migration ────────────────────────────────────


async def test_flip_updates_stored_plan_doc_when_stale():
    from services.premium_max_branches_flip_migration import (
        flip_stored_premium_max_branches,
    )

    plan_doc = {
        "_id": "plan1",
        "name": "premium",
        "tenant_caps": {"max_branches": None},
    }
    plans_col = _FakeCollection(find_one_val=plan_doc)
    fake_db = _FakeDB({"plans": plans_col})

    with (
        patch("services.premium_max_branches_flip_migration.db", fake_db),
        patch(
            "services.premium_max_branches_flip_migration.invalidate_plan_cache",
            new=AsyncMock(),
        ) as mock_invalidate,
    ):
        applied = await flip_stored_premium_max_branches()

    assert applied is True
    assert len(plans_col.updated) == 1
    filt, update = plans_col.updated[0]
    assert update["$set"]["tenant_caps.max_branches"] == 1
    mock_invalidate.assert_awaited_once_with("plan1")


async def test_flip_is_idempotent_when_already_applied():
    from services.premium_max_branches_flip_migration import (
        flip_stored_premium_max_branches,
    )

    plan_doc = {
        "_id": "plan1",
        "name": "premium",
        "tenant_caps": {"max_branches": 1},
    }
    plans_col = _FakeCollection(find_one_val=plan_doc)
    fake_db = _FakeDB({"plans": plans_col})

    with (
        patch("services.premium_max_branches_flip_migration.db", fake_db),
        patch(
            "services.premium_max_branches_flip_migration.invalidate_plan_cache",
            new=AsyncMock(),
        ) as mock_invalidate,
    ):
        applied = await flip_stored_premium_max_branches()

    assert applied is False
    assert plans_col.updated == []
    mock_invalidate.assert_not_awaited()


async def test_flip_no_op_when_plan_doc_missing():
    from services.premium_max_branches_flip_migration import (
        flip_stored_premium_max_branches,
    )

    plans_col = _FakeCollection(find_one_val=None)
    fake_db = _FakeDB({"plans": plans_col})

    with patch("services.premium_max_branches_flip_migration.db", fake_db):
        applied = await flip_stored_premium_max_branches()

    assert applied is False
    assert plans_col.updated == []
