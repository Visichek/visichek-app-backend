from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.imports import RetentionScope
from schemas.retention_policy_schema import RetentionPolicyCreate, RetentionPolicyOut


@pytest.mark.unit
class TestRetentionScopeEnum:
    def test_enum_covers_every_sweepable_collection(self):
        assert {s.value for s in RetentionScope} == {
            "visit_sessions",
            "checkins",
            "id_images",
            "visitor_profiles",
        }

    def test_create_accepts_a_valid_scope(self):
        policy = RetentionPolicyCreate(scope="checkins", retention_days=30)
        assert policy.scope is RetentionScope.CHECKINS

    def test_create_rejects_a_typo(self):
        """A typo used to create a policy that silently purged nothing."""
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(scope="visit_session", retention_days=30)

    def test_out_stays_lenient_for_legacy_rows(self):
        """Existing rows may carry an invalid scope; reads must not 500."""
        out = RetentionPolicyOut(
            _id="507f1f77bcf86cd799439011",
            tenant_id="t1",
            scope="legacy_typo",
            retention_days=30,
        )
        assert out.scope == "legacy_typo"
