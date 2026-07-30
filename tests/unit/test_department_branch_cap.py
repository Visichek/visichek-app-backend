from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from services.department_service import (
    validate_department_create,
    validate_department_update,
)

# A valid 24-char hex ObjectId string, distinct from anything a "duplicate"
# lookup would return.
_DEPT_ID = "507f1f77bcf86cd799439011"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDepartmentBranchCap:
    async def test_counts_only_the_target_branch(self):
        """Branch A being full must not block a create on branch B."""
        counter = AsyncMock(return_value=0)
        with patch("services.department_service.count_departments", counter), patch(
            "services.department_service.get_department", AsyncMock(return_value=None)
        ), patch(
            "services.department_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.department_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            await validate_department_create(
                tenant_id="t1", name="Front Office", code=None, branch_id="b2"
            )

        assert counter.await_args.args[0] == {"tenant_id": "t1", "branch_id": "b2"}

    async def test_falls_back_to_hq_when_no_branch_supplied(self):
        counter = AsyncMock(return_value=0)
        with patch("services.department_service.count_departments", counter), patch(
            "services.department_service.get_department", AsyncMock(return_value=None)
        ), patch(
            "services.department_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.department_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            resolved = await validate_department_create(
                tenant_id="t1", name="Front Office", code=None, branch_id=None
            )

        assert resolved == "hq"
        assert counter.await_args.args[0] == {"tenant_id": "t1", "branch_id": "hq"}

    async def test_cap_is_enforced_against_the_branch_count(self):
        with patch(
            "services.department_service.count_departments", AsyncMock(return_value=15)
        ), patch(
            "services.department_service.get_department", AsyncMock(return_value=None)
        ), patch(
            "services.department_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ), patch(
            "services.department_service.enforce_entity_cap",
            AsyncMock(side_effect=HTTPException(status_code=429, detail="cap")),
        ):
            with pytest.raises(HTTPException) as exc:
                await validate_department_create(
                    tenant_id="t1", name="Front Office", code=None, branch_id="b1"
                )
        assert exc.value.status_code == 429

    async def test_name_uniqueness_is_scoped_to_the_branch(self):
        """Imperial and Elvis may each have their own Front Office."""
        getter = AsyncMock(return_value=None)
        with patch(
            "services.department_service.count_departments", AsyncMock(return_value=0)
        ), patch("services.department_service.get_department", getter), patch(
            "services.department_service.enforce_entity_cap", AsyncMock()
        ), patch(
            "services.department_service.resolve_hq_branch_id",
            AsyncMock(return_value="hq"),
        ):
            await validate_department_create(
                tenant_id="t1", name="Front Office", code=None, branch_id="b2"
            )

        name_filter = getter.await_args_list[-1].args[0]
        assert name_filter["branch_id"] == "b2"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDepartmentUpdateBranchScoping:
    """validate_department_update — duplicate checks scoped to the
    department's OWN branch, so Elvis may rename a department to
    "Front Office" even though Imperial already has one."""

    async def test_allows_duplicate_name_across_branches(self):
        """The headline scenario: Elvis (branch B) renaming to a name that
        already exists on Imperial (branch A) must be allowed — the
        cross-branch "duplicate" must never even be looked up as a
        conflict, and the lookup that DOES happen must be scoped to B."""
        current = SimpleNamespace(branch_id="branch-b")
        getter = AsyncMock(side_effect=[current, None])
        with patch("services.department_service.get_department", getter):
            await validate_department_update(
                department_id=_DEPT_ID,
                tenant_id="t1",
                name="Front Office",
                code=None,
            )

        # Two calls: self-fetch, then the name-uniqueness check.
        assert getter.await_count == 2
        name_filter = getter.await_args_list[-1].args[0]
        assert name_filter["branch_id"] == "branch-b"

    async def test_rejects_duplicate_name_on_the_same_branch(self):
        """A genuine same-branch collision must still 409."""
        current = SimpleNamespace(branch_id="branch-a")
        existing = SimpleNamespace(id="other-dept")
        getter = AsyncMock(side_effect=[current, existing])
        with patch("services.department_service.get_department", getter):
            with pytest.raises(HTTPException) as exc:
                await validate_department_update(
                    department_id=_DEPT_ID,
                    tenant_id="t1",
                    name="Front Office",
                    code=None,
                )

        assert exc.value.status_code == 409
        name_filter = getter.await_args_list[-1].args[0]
        assert name_filter["branch_id"] == "branch-a"

    async def test_falls_back_to_tenant_wide_for_legacy_untagged_rows(self):
        """A row the backfill hasn't tagged yet (branch_id falsy) must fall
        back to a tenant-wide uniqueness check — branch_id must be ABSENT
        from the filter, not present-and-None (very different in Mongo)."""
        current = SimpleNamespace(branch_id=None)
        getter = AsyncMock(side_effect=[current, None])
        with patch("services.department_service.get_department", getter):
            await validate_department_update(
                department_id=_DEPT_ID,
                tenant_id="t1",
                name="Front Office",
                code=None,
            )

        name_filter = getter.await_args_list[-1].args[0]
        assert "branch_id" not in name_filter

    async def test_raises_404_when_department_missing(self):
        getter = AsyncMock(return_value=None)
        with patch("services.department_service.get_department", getter):
            with pytest.raises(HTTPException) as exc:
                await validate_department_update(
                    department_id=_DEPT_ID,
                    tenant_id="t1",
                    name="Front Office",
                    code=None,
                )

        assert exc.value.status_code == 404
        getter.assert_awaited_once()
