from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bson import ObjectId
from fastapi import HTTPException

from services.department_service import (
    enforce_department_access,
    get_accessible_department_ids,
    lock_down_to_department_cap,
)
from services.me_limitations_service import _list_locked_department_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d

        return gen()


def _fake_db(docs):
    coll = MagicMock()
    coll.find = MagicMock(return_value=_Cursor(docs))
    fake = MagicMock()
    fake.__getitem__.return_value = coll
    return fake


@pytest.mark.unit
@pytest.mark.asyncio
class TestLockedDepartmentsPerBranch:
    async def test_cap_applies_within_each_branch(self):
        """cap=2, two branches with 2 departments each: nothing is locked."""
        docs = [
            {"_id": "d1", "branch_id": "b1"},
            {"_id": "d2", "branch_id": "b1"},
            {"_id": "d3", "branch_id": "b2"},
            {"_id": "d4", "branch_id": "b2"},
        ]
        with patch("services.me_limitations_service.db", _fake_db(docs)), patch(
            "services.me_limitations_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            locked = await _list_locked_department_ids("t1")

        assert locked == []

    async def test_overflow_within_one_branch_is_locked(self):
        docs = [
            {"_id": "d1", "branch_id": "b1"},
            {"_id": "d2", "branch_id": "b1"},
            {"_id": "d3", "branch_id": "b1"},
            {"_id": "d4", "branch_id": "b2"},
        ]
        with patch("services.me_limitations_service.db", _fake_db(docs)), patch(
            "services.me_limitations_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            locked = await _list_locked_department_ids("t1")

        assert locked == ["d3"]

    async def test_branch_null_rows_group_together(self):
        """Legacy rows the backfill has not tagged yet must not each form
        their own uncapped bucket."""
        docs = [
            {"_id": "d1", "branch_id": None},
            {"_id": "d2"},
            {"_id": "d3", "branch_id": None},
        ]
        with patch("services.me_limitations_service.db", _fake_db(docs)), patch(
            "services.me_limitations_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            locked = await _list_locked_department_ids("t1")

        assert locked == ["d3"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestAccessibleDepartmentsPerBranch:
    """``get_accessible_department_ids`` gates real reads/writes via
    ``enforce_department_access`` / ``lock_down_to_department_cap``. It is
    the accessible-side inverse of ``_list_locked_department_ids``: same
    query, same sort, same ``branch_id`` bucketing, but keeps a branch's
    oldest ``cap`` departments instead of excluding the overflow.
    """

    async def test_accessible_per_branch_partitioning(self):
        """cap=2, two branches with 2 departments each: all four accessible.

        This is the case the old tenant-wide ``.limit(int(cap))`` broke —
        it would have returned only the two globally-oldest rows (d1, d2)
        and locked d3/d4 out despite each branch being within its own cap.
        """
        docs = [
            {"_id": "d1", "branch_id": "b1"},
            {"_id": "d2", "branch_id": "b1"},
            {"_id": "d3", "branch_id": "b2"},
            {"_id": "d4", "branch_id": "b2"},
        ]
        with patch("core.database.db", _fake_db(docs)), patch(
            "services.plan_cache_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            accessible = await get_accessible_department_ids("t1")

        assert accessible == {"d1", "d2", "d3", "d4"}

    async def test_accessible_overflow_within_one_branch_excluded(self):
        """cap=2, three on branch A + one on branch B: only the third on A
        is excluded; the other three stay accessible."""
        docs = [
            {"_id": "d1", "branch_id": "b1"},
            {"_id": "d2", "branch_id": "b1"},
            {"_id": "d3", "branch_id": "b1"},
            {"_id": "d4", "branch_id": "b2"},
        ]
        with patch("core.database.db", _fake_db(docs)), patch(
            "services.plan_cache_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            accessible = await get_accessible_department_ids("t1")

        assert accessible == {"d1", "d2", "d4"}

    async def test_accessible_none_when_no_cap_configured(self):
        """No ``max_departments`` cap => ``None`` (everything accessible),
        distinct from an empty set. Callers branch on ``is None``."""
        with patch(
            "services.plan_cache_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {}}),
        ):
            accessible = await get_accessible_department_ids("t1")

        assert accessible is None

    async def test_accessible_null_branch_rows_share_bucket(self):
        """Legacy untagged rows must be capped together, not each treated
        as its own uncapped branch."""
        docs = [
            {"_id": "d1", "branch_id": None},
            {"_id": "d2"},
            {"_id": "d3", "branch_id": None},
        ]
        with patch("core.database.db", _fake_db(docs)), patch(
            "services.plan_cache_service.resolve_tenant_plan",
            AsyncMock(return_value={"tenant_caps": {"max_departments": 2}}),
        ):
            accessible = await get_accessible_department_ids("t1")

        assert accessible == {"d1", "d2"}


@pytest.mark.unit
@pytest.mark.asyncio
class TestLockedAndAccessibleAreComplementary:
    """``_list_locked_department_ids`` and ``get_accessible_department_ids``
    must partition the same input into exactly two disjoint, exhaustive
    sets — every department id appears in one and only one result."""

    async def test_every_id_appears_in_exactly_one_result(self):
        docs = [
            {"_id": "d1", "branch_id": "b1"},
            {"_id": "d2", "branch_id": "b1"},
            {"_id": "d3", "branch_id": "b1"},
            {"_id": "d4", "branch_id": "b2"},
            {"_id": "d5", "branch_id": "b2"},
            {"_id": "d6", "branch_id": None},
            {"_id": "d7"},
            {"_id": "d8", "branch_id": None},
        ]
        all_ids = {d["_id"] for d in docs}
        plan = {"tenant_caps": {"max_departments": 2}}

        with patch("services.me_limitations_service.db", _fake_db(docs)), patch(
            "services.me_limitations_service.resolve_tenant_plan",
            AsyncMock(return_value=plan),
        ):
            locked = set(await _list_locked_department_ids("t1"))

        with patch("core.database.db", _fake_db(docs)), patch(
            "services.plan_cache_service.resolve_tenant_plan",
            AsyncMock(return_value=plan),
        ):
            accessible = await get_accessible_department_ids("t1")

        assert accessible is not None
        assert locked | accessible == all_ids
        assert locked & accessible == set()


@pytest.mark.unit
@pytest.mark.asyncio
class TestEnforceDepartmentAccess:
    async def test_no_cap_allows_everything(self):
        with patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value=None),
        ):
            # Must not raise.
            await enforce_department_access("t1", "any-department")

    async def test_accessible_department_passes(self):
        with patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value={"d1", "d2"}),
        ):
            await enforce_department_access("t1", "d1")

    async def test_locked_department_raises_403(self):
        with patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value={"d1"}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_department_access("t1", "d3")
        assert exc_info.value.status_code == 403
        assert "locked" in str(exc_info.value.detail)

    async def test_empty_accessible_set_locks_everything(self):
        """An empty set is a real cap result, not 'no cap' — every id 403s."""
        with patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value=set()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_department_access("t1", "d1")
        assert exc_info.value.status_code == 403


def _fake_db_update(modified_count: int):
    """Fake db whose departments collection records update_many calls."""
    result = MagicMock()
    result.modified_count = modified_count
    coll = MagicMock()
    coll.update_many = AsyncMock(return_value=result)
    fake = MagicMock()
    fake.__getitem__.return_value = coll
    return fake, coll


@pytest.mark.unit
@pytest.mark.asyncio
class TestLockDownToDepartmentCap:
    async def test_no_cap_returns_zero_without_touching_db(self):
        fake, coll = _fake_db_update(0)
        with patch("core.database.db", fake), patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value=None),
        ):
            count = await lock_down_to_department_cap("t1")
        assert count == 0
        coll.update_many.assert_not_awaited()

    async def test_excess_departments_deactivated(self):
        keep_a = str(ObjectId())
        keep_b = str(ObjectId())
        fake, coll = _fake_db_update(3)
        with patch("core.database.db", fake), patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value={keep_a, keep_b}),
        ):
            count = await lock_down_to_department_cap("t1")

        assert count == 3
        args, _ = coll.update_many.await_args
        filter_doc, update_doc = args
        assert filter_doc["tenant_id"] == "t1"
        # Only active rows outside the accessible set are flipped.
        assert filter_doc["is_active"] is True
        nin = {str(oid) for oid in filter_doc["_id"]["$nin"]}
        assert nin == {keep_a, keep_b}
        assert update_doc["$set"]["is_active"] is False
        assert "last_updated" in update_doc["$set"]

    async def test_invalid_object_ids_are_skipped_not_fatal(self):
        """Non-ObjectId ids in the accessible set (legacy string ids) must
        not crash the lockdown — they are simply left out of the $nin."""
        valid = str(ObjectId())
        fake, coll = _fake_db_update(0)
        with patch("core.database.db", fake), patch(
            "services.department_service.get_accessible_department_ids",
            AsyncMock(return_value={valid, "not-an-objectid"}),
        ):
            count = await lock_down_to_department_cap("t1")

        assert count == 0
        args, _ = coll.update_many.await_args
        nin = {str(oid) for oid in args[0]["_id"]["$nin"]}
        assert nin == {valid}
