from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
