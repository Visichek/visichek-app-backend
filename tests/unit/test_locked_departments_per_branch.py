from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.department_service import get_accessible_department_ids
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
