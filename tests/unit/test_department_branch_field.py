from __future__ import annotations

import pytest

from schemas.department_schema import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
)
from services.branch_backfill import _branch_scoped_collections


@pytest.mark.unit
class TestDepartmentBranchField:
    def test_create_accepts_branch_id(self):
        dept = DepartmentCreate(tenant_id="t1", name="Front Office", branch_id="b1")
        assert dept.branch_id == "b1"

    def test_branch_id_is_optional_for_single_branch_tenants(self):
        """A tenant with only the default HQ branch never supplies one —
        the service resolves HQ for them."""
        dept = DepartmentCreate(tenant_id="t1", name="Front Office")
        assert dept.branch_id is None

    def test_update_can_move_a_department(self):
        assert "branch_id" in DepartmentUpdate.model_fields

    def test_out_exposes_branch_id(self):
        assert "branch_id" in DepartmentOut.model_fields

    def test_backfill_tags_legacy_departments_to_hq(self):
        assert "departments" in _branch_scoped_collections
