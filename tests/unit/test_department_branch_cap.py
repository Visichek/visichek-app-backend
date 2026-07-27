from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from services.department_service import validate_department_create


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
