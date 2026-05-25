"""Unit tests for the tenant_id-from-token fix and tenant-scoped read auditing.

Two related changes are covered here, both pure unit tests (no live Mongo /
Redis / ASGI loop):

  * tenant_id is server-assigned from the auth token, never client-supplied.
    The create-request schemas must therefore accept a payload WITHOUT a
    tenant_id (previously a required field that 422'd the request before the
    route could inject the token's tenant_id).
  * Tenant-scoped GETs are recorded to the audit trail via
    ``services.audit_service.record_read_audit`` (wired into
    ``PlanEnforcementMiddleware``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# tenant_id is optional on create-request schemas (token-derived)
# ---------------------------------------------------------------------------


class TestTenantIdOptionalOnCreateSchemas:
    """Every tenant-scoped create schema must build without a client tenant_id."""

    def test_privacy_notice_create_without_tenant_id(self):
        from schemas.privacy_notice_schema import PrivacyNoticeCreate

        notice = PrivacyNoticeCreate(title="Visitor privacy notice", summary="A")
        assert notice.tenant_id == ""

    def test_appointment_create_without_tenant_id(self):
        from schemas.appointment_schema import AppointmentCreate

        appt = AppointmentCreate(
            host_id="h1", department_id="d1", scheduled_datetime=1779667200
        )
        assert appt.tenant_id == ""

    def test_dsr_create_without_tenant_id(self):
        from schemas.data_subject_request_schema import DSRCreate, DSRType

        dsr = DSRCreate(visitor_profile_id="vp1", request_type=DSRType.ACCESS)
        assert dsr.tenant_id == ""

    def test_retention_policy_create_without_tenant_id(self):
        from schemas.retention_policy_schema import RetentionPolicyCreate

        policy = RetentionPolicyCreate(scope="visit_sessions", retention_days=30)
        assert policy.tenant_id == ""

    def test_dpr_create_without_tenant_id(self):
        from schemas.data_processing_register_schema import DPRCreate, LawfulBasis

        dpr = DPRCreate(
            field_name="email", purpose="contact", lawful_basis=LawfulBasis.CONSENT
        )
        assert dpr.tenant_id == ""

    def test_checkin_config_create_without_tenant_id(self):
        from schemas.checkin_config_schema import CheckinConfigCreate

        cfg = CheckinConfigCreate(required_fields=[])
        assert cfg.tenant_id == ""

    def test_client_supplied_tenant_id_is_still_accepted_then_overwritten(self):
        """The field still ACCEPTS a value (the route overwrites it with the
        token's tenant_id); it just no longer REQUIRES one on the wire."""
        from schemas.privacy_notice_schema import PrivacyNoticeCreate

        notice = PrivacyNoticeCreate(title="x", tenant_id="forged-by-client")
        # Schema keeps whatever was passed; the route layer is responsible for
        # overwriting it from principal.tenant_id before the writer runs.
        assert notice.tenant_id == "forged-by-client"


# ---------------------------------------------------------------------------
# Read-audit helpers
# ---------------------------------------------------------------------------


class TestReadAuditHelpers:
    def test_should_audit_read_for_normal_collection(self):
        from services.audit_service import should_audit_read

        assert should_audit_read("privacy_notices") is True
        assert should_audit_read("appointments") is True

    def test_should_not_audit_read_for_excluded_collections(self):
        from services.audit_service import should_audit_read

        assert should_audit_read("audit") is False
        assert should_audit_read("dashboard") is False

    def test_should_not_audit_read_for_empty_collection(self):
        from services.audit_service import should_audit_read

        assert should_audit_read(None) is False
        assert should_audit_read("") is False

    def test_resource_id_extracted_from_detail_path(self):
        from services.audit_service import _resource_id_from_path

        oid = "507f1f77bcf86cd799439011"
        assert _resource_id_from_path(f"/v1/privacy-notices/{oid}") == oid

    def test_resource_id_empty_for_list_path(self):
        from services.audit_service import _resource_id_from_path

        assert _resource_id_from_path("/v1/privacy-notices") == ""

    def test_resource_id_empty_for_named_subpath(self):
        from services.audit_service import _resource_id_from_path

        # "/active" is not an ObjectId, so no resource_id is recorded.
        assert _resource_id_from_path("/v1/privacy-notices/active") == ""


class TestRecordReadAudit:
    @pytest.mark.asyncio
    async def test_records_event_for_tenant_scoped_read(self):
        with patch(
            "services.audit_service.record_audit_event", new_callable=AsyncMock
        ) as mock_record:
            from services.audit_service import record_read_audit

            await record_read_audit(
                actor_id="user-1",
                actor_role="super_admin",
                tenant_id="tenant-1",
                collection="privacy_notices",
                path="/v1/privacy-notices/507f1f77bcf86cd799439011",
                request_id="req-1",
            )

        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["action"] == "privacy_notices.read"
        assert kwargs["resource_type"] == "privacy_notices"
        assert kwargs["resource_id"] == "507f1f77bcf86cd799439011"
        assert kwargs["tenant_id"] == "tenant-1"
        assert kwargs["actor_id"] == "user-1"
        assert kwargs["details"]["method"] == "GET"

    @pytest.mark.asyncio
    async def test_skips_excluded_collection(self):
        with patch(
            "services.audit_service.record_audit_event", new_callable=AsyncMock
        ) as mock_record:
            from services.audit_service import record_read_audit

            await record_read_audit(
                actor_id="user-1",
                actor_role="auditor",
                tenant_id="tenant-1",
                collection="audit",
                path="/v1/audit",
            )

        mock_record.assert_not_awaited()
