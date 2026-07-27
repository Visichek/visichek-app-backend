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

    def test_update_cannot_set_branch_id(self):
        """branch_id is deliberately absent from DepartmentUpdate — see
        FIX 2 / schemas/department_schema.py. Create resolves and
        validates the branch (ownership + per-branch max_departments cap);
        update never re-checks either, so forwarding an arbitrary
        client-supplied branch_id would let a caller escape the cap by
        inventing a new bucket per PATCH. Repository-level $set already
        drops None values, so the field could never have been used to
        clear a branch either — it bought nothing but a security hole."""
        assert "branch_id" not in DepartmentUpdate.model_fields

    def test_out_exposes_branch_id(self):
        assert "branch_id" in DepartmentOut.model_fields

    def test_backfill_tags_legacy_departments_to_hq(self):
        assert "departments" in _branch_scoped_collections
