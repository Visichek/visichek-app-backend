from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.v1.incident_route import INCIDENTS_LIST_SPEC
from schemas.imports import IncidentType, RiskLevel
from schemas.incident_log_schema import IncidentLogCreate, IncidentLogOut
from services.branch_backfill import _branch_scoped_collections


@pytest.mark.unit
class TestIncidentBranchAttribution:
    def test_create_carries_branch_id(self):
        incident = IncidentLogCreate(
            tenant_id="t1",
            reported_by="u1",
            incident_type=IncidentType.DATA_BREACH,
            description="test",
            branch_id="b1",
        )
        assert incident.branch_id == "b1"

    def test_out_exposes_branch_id(self):
        assert "branch_id" in IncidentLogOut.model_fields

    def test_list_spec_allows_filtering_by_branch(self):
        assert "branchId" in INCIDENTS_LIST_SPEC.filters

    def test_backfill_tags_legacy_incidents_to_hq(self):
        assert "incident_logs" in _branch_scoped_collections


@pytest.mark.unit
class TestRiskLevelEnum:
    def test_accepts_a_valid_level(self):
        incident = IncidentLogCreate(
            tenant_id="t1",
            reported_by="u1",
            incident_type=IncidentType.DATA_BREACH,
            description="test",
            risk_level=RiskLevel.HIGH,
        )
        assert incident.risk_level is RiskLevel.HIGH

    def test_rejects_free_text(self):
        with pytest.raises(ValidationError):
            IncidentLogCreate(
                tenant_id="t1",
                reported_by="u1",
                incident_type=IncidentType.DATA_BREACH,
                description="test",
                risk_level="quite bad actually",
            )

    def test_72h_ndpc_deadline_still_defaults(self):
        incident = IncidentLogCreate(
            tenant_id="t1",
            reported_by="u1",
            incident_type=IncidentType.DATA_BREACH,
            description="test",
        )
        assert incident.notification_deadline == incident.date_created + 72 * 3600
