from __future__ import annotations

import pytest
import time
from bson import ObjectId
from pydantic import ValidationError

from schemas.tenant_schema import (
    TenantCreate,
    TenantUpdate,
    TenantOut,
    TenantBootstrapRequest,
)
from schemas.department_schema import DepartmentCreate, DepartmentUpdate, DepartmentOut
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
    SystemUserTenantLogin,
)
from schemas.admin_schema import AdminLogin
from schemas.user_schema import UserLogin
from schemas.visitor_profile_schema import (
    VisitorProfileCreate,
    VisitorProfileUpdate,
    VisitorProfileOut,
)
from schemas.visit_session_schema import (
    VisitSessionCreate,
    VisitSessionUpdate,
    VisitSessionOut,
    CheckInRequest,
    CheckOutRequest,
)
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentOut,
)
from schemas.privacy_notice_schema import (
    PrivacyNoticeCreate,
    PrivacyNoticeUpdate,
    PrivacyNoticeOut,
)
from schemas.audit_log_schema import AuditLogCreate, AuditLogOut
from schemas.incident_log_schema import (
    IncidentLogCreate,
    IncidentLogUpdate,
    IncidentLogOut,
)
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate, DSROut
from schemas.retention_policy_schema import (
    RetentionPolicyCreate,
    RetentionPolicyUpdate,
    RetentionPolicyOut,
)
from schemas.sub_processor_schema import (
    SubProcessorCreate,
    SubProcessorUpdate,
    SubProcessorOut,
)
from schemas.deletion_log_schema import DeletionLogCreate, DeletionLogOut
from schemas.user_session_schema import (
    UserSessionCreate,
    UserSessionUpdate,
    UserSessionOut,
)
from schemas.imports import (
    LawfulBasis,
    NoticeDisplayMode,
    DeletionAction,
    SystemUserRole,
    AccountStatus,
    VisitStatus,
    CheckInMethod,
    CheckOutMethod,
    VerificationMethod,
    VerificationStatus,
    AppointmentStatus,
    ProfilingPreference,
    IncidentType,
    IncidentStatus,
    BadgeFormat,
    DSRType,
    DSRStatus,
)


# ============================================================================
# TENANT SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestTenantSchema:
    def test_tenant_create_with_required_fields(self):
        """Test TenantCreate with minimal required fields."""
        payload = TenantCreate(company_name="Acme Corp")
        assert payload.company_name == "Acme Corp"
        assert payload.lawful_basis == LawfulBasis.LEGITIMATE_INTEREST
        assert payload.notice_display_mode == NoticeDisplayMode.PASSIVE
        assert payload.retention_days == 1095
        assert payload.default_retention_action == DeletionAction.ANONYMISE
        assert payload.cross_border_approved is False
        assert payload.is_active is True
        assert isinstance(payload.date_created, int)
        assert isinstance(payload.last_updated, int)

    def test_tenant_create_auto_timestamps(self):
        """Test TenantCreate generates timestamps automatically."""
        before = int(time.time())
        payload = TenantCreate(company_name="Test Co")
        after = int(time.time())
        assert before <= payload.date_created <= after
        assert before <= payload.last_updated <= after

    def test_tenant_create_with_optional_fields(self):
        """Test TenantCreate with all optional fields provided."""
        int(time.time())
        payload = TenantCreate(
            company_name="Test Co",
            lawful_basis=LawfulBasis.CONSENT,
            notice_display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
            retention_days=365,
            default_retention_action=DeletionAction.DELETE,
            dpo_contact_email="dpo@example.com",
            privacy_policy_url="https://example.com/privacy",
            country_of_hosting="US",
            cross_border_approved=True,
            is_active=False,
            active_notice_version="v2",
        )
        assert payload.company_name == "Test Co"
        assert payload.lawful_basis == LawfulBasis.CONSENT
        assert payload.notice_display_mode == NoticeDisplayMode.ACTIVE_CONSENT
        assert payload.retention_days == 365
        assert payload.default_retention_action == DeletionAction.DELETE
        assert payload.dpo_contact_email == "dpo@example.com"
        assert payload.privacy_policy_url == "https://example.com/privacy"
        assert payload.country_of_hosting == "US"
        assert payload.cross_border_approved is True
        assert payload.is_active is False
        assert payload.active_notice_version == "v2"

    def test_tenant_create_missing_required_fields(self):
        """Test TenantCreate fails without required fields."""
        with pytest.raises(ValidationError) as exc_info:
            TenantCreate()
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("company_name",) for e in errors)

    def test_tenant_update_all_optional(self):
        """Test TenantUpdate makes all fields optional."""
        payload = TenantUpdate()
        assert payload.company_name is None
        assert payload.lawful_basis is None
        assert payload.notice_display_mode is None
        assert isinstance(payload.last_updated, int)

    def test_tenant_update_partial(self):
        """Test TenantUpdate with partial updates."""
        payload = TenantUpdate(
            company_name="Updated Co",
            retention_days=730,
            dpo_contact_email="new-dpo@example.com",
        )
        assert payload.company_name == "Updated Co"
        assert payload.retention_days == 730
        assert payload.dpo_contact_email == "new-dpo@example.com"
        assert payload.lawful_basis is None
        assert payload.notice_display_mode is None

    def test_tenant_out_objectid_conversion(self):
        """Test TenantOut converts ObjectId _id to string id."""
        oid = ObjectId()
        data = {
            "_id": oid,
            "company_name": "Test Co",
            "lawful_basis": "legitimate_interest",
            "notice_display_mode": "passive",
            "retention_days": 1095,
            "default_retention_action": "anonymise",
            "cross_border_approved": False,
            "is_active": True,
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        out = TenantOut(**data)
        assert out.id == str(oid)
        assert isinstance(out.id, str)

    def test_tenant_out_populate_by_name(self):
        """Test TenantOut accepts both _id and id fields."""
        oid = ObjectId()
        data_with_id = {
            "id": str(oid),
            "company_name": "Test Co",
            "lawful_basis": "legitimate_interest",
            "notice_display_mode": "passive",
            "retention_days": 1095,
            "default_retention_action": "anonymise",
            "cross_border_approved": False,
            "is_active": True,
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        out = TenantOut(**data_with_id)
        assert out.id == str(oid)

    def test_tenant_enum_validation(self):
        """Test TenantCreate validates enum fields."""
        with pytest.raises(ValidationError):
            TenantCreate(company_name="Test", lawful_basis="invalid_basis")
        with pytest.raises(ValidationError):
            TenantCreate(company_name="Test", notice_display_mode="invalid_mode")
        with pytest.raises(ValidationError):
            TenantCreate(company_name="Test", default_retention_action="invalid_action")


# ============================================================================
# DEPARTMENT SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestDepartmentSchema:
    def test_department_create_with_required_fields(self):
        """Test DepartmentCreate with minimal required fields."""
        payload = DepartmentCreate(
            tenant_id="tenant123", code="DEPT001", name="Engineering"
        )
        assert payload.tenant_id == "tenant123"
        assert payload.code == "DEPT001"
        assert payload.name == "Engineering"
        assert payload.is_active is True
        assert payload.created_by is None
        assert isinstance(payload.date_created, int)
        assert isinstance(payload.last_updated, int)

    def test_department_create_auto_timestamps(self):
        """Test DepartmentCreate generates timestamps."""
        before = int(time.time())
        payload = DepartmentCreate(
            tenant_id="tenant123", code="DEPT001", name="Engineering"
        )
        after = int(time.time())
        assert before <= payload.date_created <= after
        assert before <= payload.last_updated <= after

    def test_department_create_missing_required(self):
        """Test DepartmentCreate fails without required fields."""
        with pytest.raises(ValidationError) as exc_info:
            DepartmentCreate(tenant_id="tenant123")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors)

    def test_department_update_all_optional(self):
        """Test DepartmentUpdate makes all fields optional."""
        payload = DepartmentUpdate()
        assert payload.code is None
        assert payload.name is None
        assert payload.is_active is None
        assert isinstance(payload.last_updated, int)

    def test_department_update_partial(self):
        """Test DepartmentUpdate with partial updates."""
        payload = DepartmentUpdate(name="Updated Dept", is_active=False)
        assert payload.name == "Updated Dept"
        assert payload.is_active is False
        assert payload.code is None

    def test_department_out_objectid_conversion(self):
        """Test DepartmentOut converts ObjectId."""
        oid = ObjectId()
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "code": "DEPT001",
            "name": "Engineering",
            "is_active": True,
            "created_by": "user456",
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        out = DepartmentOut(**data)
        assert out.id == str(oid)

    def test_department_out_populate_by_name(self):
        """Test DepartmentOut accepts id field."""
        oid = str(ObjectId())
        data = {
            "id": oid,
            "tenant_id": "tenant123",
            "code": "DEPT001",
            "name": "Engineering",
            "is_active": True,
            "created_by": "user456",
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        out = DepartmentOut(**data)
        assert out.id == oid


# ============================================================================
# SYSTEM USER SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestSystemUserSchema:
    def test_system_user_create_required_fields(self):
        """Test SystemUserCreate with required fields."""
        payload = SystemUserCreate(
            tenant_id="tenant123",
            full_name="John Doe",
            email="john@example.com",
            role=SystemUserRole.RECEPTIONIST,
            password_hash="MyStr0ng!Passw0rd#2026",
        )
        assert payload.tenant_id == "tenant123"
        assert payload.full_name == "John Doe"
        assert payload.email == "john@example.com"
        assert payload.role == SystemUserRole.RECEPTIONIST
        assert payload.account_status == AccountStatus.ACTIVE
        assert payload.is_active is True
        assert payload.password_hash != "MyStr0ng!Passw0rd#2026"  # Should be hashed

    def test_system_user_create_password_hashing(self):
        """Test SystemUserCreate hashes password via validator."""
        raw_password = "MyStr0ng!Passw0rd#2026"
        payload = SystemUserCreate(
            tenant_id="tenant123",
            full_name="John Doe",
            email="john@example.com",
            role=SystemUserRole.RECEPTIONIST,
            password_hash=raw_password,
        )
        assert payload.password_hash != raw_password
        assert len(payload.password_hash) > len(raw_password)

    def test_system_user_create_auto_timestamps(self):
        """Test SystemUserCreate generates timestamps."""
        before = int(time.time())
        payload = SystemUserCreate(
            tenant_id="tenant123",
            full_name="John Doe",
            email="john@example.com",
            role=SystemUserRole.SUPER_ADMIN,
            password_hash="MyStr0ng!Passw0rd#2026",
        )
        after = int(time.time())
        assert before <= payload.date_created <= after
        assert before <= payload.last_updated <= after

    def test_system_user_create_invalid_email(self):
        """Test SystemUserCreate rejects invalid email."""
        with pytest.raises(ValidationError) as exc_info:
            SystemUserCreate(
                tenant_id="tenant123",
                full_name="John Doe",
                email="not-an-email",
                role=SystemUserRole.RECEPTIONIST,
                password_hash="password",
            )
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("email",) for e in errors)

    def test_system_user_create_invalid_role(self):
        """Test SystemUserCreate validates role enum."""
        with pytest.raises(ValidationError) as exc_info:
            SystemUserCreate(
                tenant_id="tenant123",
                full_name="John Doe",
                email="john@example.com",
                role="invalid_role",
                password_hash="password",
            )
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("role",) for e in errors)

    def test_system_user_update_all_optional(self):
        """Test SystemUserUpdate makes all fields optional."""
        payload = SystemUserUpdate()
        assert payload.full_name is None
        assert payload.email is None
        assert payload.department_id is None
        assert payload.role is None
        assert payload.account_status is None
        assert isinstance(payload.last_updated, int)

    def test_system_user_update_partial(self):
        """Test SystemUserUpdate with partial updates."""
        payload = SystemUserUpdate(
            full_name="Jane Doe",
            email="jane@example.com",
            role=SystemUserRole.DPO,
            account_status=AccountStatus.SUSPENDED,
        )
        assert payload.full_name == "Jane Doe"
        assert payload.email == "jane@example.com"
        assert payload.role == SystemUserRole.DPO
        assert payload.account_status == AccountStatus.SUSPENDED
        assert payload.department_id is None

    def test_system_user_login_schema(self):
        """Test SystemUserLogin schema."""
        payload = SystemUserLogin(email="john@example.com", password="password123")
        assert payload.email == "john@example.com"
        assert payload.password == "password123"

    def test_system_user_out_objectid_conversion(self):
        """Test SystemUserOut converts ObjectId."""
        oid = ObjectId()
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "full_name": "John Doe",
            "email": "john@example.com",
            "role": "receptionist",
            "account_status": "ACTIVE",
            "is_active": True,
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        out = SystemUserOut(**data)
        assert out.id == str(oid)

    def test_system_user_out_with_tokens(self):
        """Test SystemUserOut includes optional tokens."""
        data = {
            "tenant_id": "tenant123",
            "full_name": "John Doe",
            "email": "john@example.com",
            "role": "receptionist",
            "account_status": "ACTIVE",
            "is_active": True,
            "access_token": "jwt_access_token",
            "refresh_token": "jwt_refresh_token",
        }
        out = SystemUserOut(**data)
        assert out.access_token == "jwt_access_token"
        assert out.refresh_token == "jwt_refresh_token"


# ============================================================================
# VISITOR PROFILE SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestVisitorProfileSchema:
    def test_visitor_profile_create_required_fields(self):
        """Test VisitorProfileCreate with required fields."""
        payload = VisitorProfileCreate(tenant_id="tenant123", full_name="Jane Smith")
        assert payload.tenant_id == "tenant123"
        assert payload.full_name == "Jane Smith"
        assert payload.profiling_preference == ProfilingPreference.ALLOWED
        assert payload.phone is None
        assert payload.email_address is None
        assert isinstance(payload.date_created, int)
        assert isinstance(payload.last_updated, int)

    def test_visitor_profile_create_with_all_fields(self):
        """Test VisitorProfileCreate with all optional fields."""
        payload = VisitorProfileCreate(
            tenant_id="tenant123",
            full_name="Jane Smith",
            phone="+1234567890",
            email_address="jane@example.com",
            company="Smith Enterprises",
            photo_object_key="photo123.jpg",
            id_type="passport",
            id_number="ABC123456",
            id_image_object_key="id_image123.jpg",
            profiling_preference=ProfilingPreference.OPTED_OUT,
            last_verification_date=int(time.time()),
        )
        assert payload.phone == "+1234567890"
        assert payload.email_address == "jane@example.com"
        assert payload.company == "Smith Enterprises"
        assert payload.profiling_preference == ProfilingPreference.OPTED_OUT

    def test_visitor_profile_create_invalid_email(self):
        """Test VisitorProfileCreate rejects invalid email."""
        with pytest.raises(ValidationError):
            VisitorProfileCreate(
                tenant_id="tenant123",
                full_name="Jane Smith",
                email_address="not-an-email",
            )

    def test_visitor_profile_update_all_optional(self):
        """Test VisitorProfileUpdate makes all fields optional."""
        payload = VisitorProfileUpdate()
        assert payload.phone is None
        assert payload.full_name is None
        assert payload.profiling_preference is None
        assert isinstance(payload.last_updated, int)

    def test_visitor_profile_update_partial(self):
        """Test VisitorProfileUpdate with partial updates."""
        payload = VisitorProfileUpdate(
            full_name="Jane Smith Updated",
            phone="+9999999999",
            profiling_preference=ProfilingPreference.OPTED_OUT,
        )
        assert payload.full_name == "Jane Smith Updated"
        assert payload.phone == "+9999999999"
        assert payload.profiling_preference == ProfilingPreference.OPTED_OUT
        assert payload.company is None

    def test_visitor_profile_out_objectid_conversion(self):
        """Test VisitorProfileOut converts ObjectId."""
        oid = ObjectId()
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "full_name": "Jane Smith",
            "profiling_preference": "allowed",
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
            "total_visits": 5,
            "last_visit_date": int(time.time()),
        }
        out = VisitorProfileOut(**data)
        assert out.id == str(oid)
        assert out.total_visits == 5

    def test_visitor_profile_enum_validation(self):
        """Test VisitorProfileCreate validates profiling preference."""
        with pytest.raises(ValidationError):
            VisitorProfileCreate(
                tenant_id="tenant123",
                full_name="Jane Smith",
                profiling_preference="invalid_preference",
            )


# ============================================================================
# VISIT SESSION SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestVisitSessionSchema:
    def test_visit_session_create_required_fields(self):
        """Test VisitSessionCreate with required fields."""
        payload = VisitSessionCreate(
            tenant_id="tenant123",
            visitor_profile_id="visitor456",
            department_id="dept789",
        )
        assert payload.tenant_id == "tenant123"
        assert payload.visitor_profile_id == "visitor456"
        assert payload.department_id == "dept789"
        assert payload.status == VisitStatus.REGISTERED
        assert payload.verification_status == VerificationStatus.UNVERIFIED
        assert payload.consent_notice_displayed is False
        assert isinstance(payload.check_in_time, int)
        assert isinstance(payload.date_created, int)

    def test_visit_session_create_with_optional_fields(self):
        """Test VisitSessionCreate with optional fields."""
        int(time.time())
        payload = VisitSessionCreate(
            tenant_id="tenant123",
            visitor_profile_id="visitor456",
            department_id="dept789",
            host_id="host111",
            check_in_method=CheckInMethod.QR,
            status=VisitStatus.CHECKED_IN,
            verification_method=VerificationMethod.ID_SCAN,
            badge_qr_token="qr_token_123",
            badge_format=BadgeFormat.A6,
        )
        assert payload.host_id == "host111"
        assert payload.check_in_method == CheckInMethod.QR
        assert payload.status == VisitStatus.CHECKED_IN
        assert payload.verification_method == VerificationMethod.ID_SCAN

    def test_visit_session_update_all_optional(self):
        """Test VisitSessionUpdate makes all fields optional."""
        payload = VisitSessionUpdate()
        assert payload.status is None
        assert payload.check_out_method is None
        assert payload.check_out_time is None
        assert isinstance(payload.last_updated, int)

    def test_visit_session_update_partial(self):
        """Test VisitSessionUpdate with partial updates."""
        now = int(time.time())
        payload = VisitSessionUpdate(
            status=VisitStatus.CHECKED_OUT,
            check_out_method=CheckOutMethod.QR_SCAN,
            check_out_time=now,
            consent_granted=True,
        )
        assert payload.status == VisitStatus.CHECKED_OUT
        assert payload.check_out_method == CheckOutMethod.QR_SCAN
        assert payload.check_out_time == now
        assert payload.consent_granted is True

    def test_visit_session_out_objectid_conversion(self):
        """Test VisitSessionOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "visitor_profile_id": "visitor456",
            "department_id": "dept789",
            "status": "checked_out",
            "verification_status": "verified",
            "check_in_time": now - 3600,
            "check_out_time": now,
            "date_created": now,
        }
        out = VisitSessionOut(**data)
        assert out.id == str(oid)
        assert out.visit_duration == 3600

    def test_visit_session_out_visit_duration_computed(self):
        """Test VisitSessionOut computes visit duration."""
        now = int(time.time())
        check_in = now - 7200  # 2 hours ago
        check_out = now
        data = {
            "tenant_id": "tenant123",
            "visitor_profile_id": "visitor456",
            "department_id": "dept789",
            "status": "checked_out",
            "verification_status": "verified",
            "check_in_time": check_in,
            "check_out_time": check_out,
        }
        out = VisitSessionOut(**data)
        assert out.visit_duration == 7200

    def test_checkin_request_defaults(self):
        """Test CheckInRequest with defaults."""
        payload = CheckInRequest(
            department_id="dept789", phone="+2348012345678", full_name="Jane Smith"
        )
        assert payload.department_id == "dept789"
        assert payload.check_in_method == CheckInMethod.MANUAL
        assert payload.phone == "+2348012345678"
        assert payload.full_name == "Jane Smith"

    def test_checkout_request_defaults(self):
        """Test CheckOutRequest with defaults."""
        payload = CheckOutRequest()
        assert payload.check_out_method == CheckOutMethod.QR_SCAN
        assert payload.badge_qr_token is None
        assert payload.session_id is None

    def test_visit_session_enum_validation(self):
        """Test VisitSessionCreate validates enum fields."""
        with pytest.raises(ValidationError):
            VisitSessionCreate(
                tenant_id="tenant123",
                visitor_profile_id="visitor456",
                department_id="dept789",
                status="invalid_status",
            )


# ============================================================================
# APPOINTMENT SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestAppointmentSchema:
    def test_appointment_create_required_fields(self):
        """Test AppointmentCreate with required fields."""
        now = int(time.time())
        payload = AppointmentCreate(
            tenant_id="tenant123",
            host_id="host111",
            department_id="dept789",
            scheduled_datetime=now + 3600,
            visitor_name_snapshot="Jane Smith",
            visitor_phone="+2348012345678",
        )
        assert payload.tenant_id == "tenant123"
        assert payload.host_id == "host111"
        assert payload.department_id == "dept789"
        assert payload.scheduled_datetime == now + 3600
        assert payload.visitor_name_snapshot == "Jane Smith"
        assert payload.visitor_phone == "+2348012345678"
        assert payload.status == AppointmentStatus.SCHEDULED
        assert isinstance(payload.date_created, int)
        assert isinstance(payload.last_updated, int)

    def test_appointment_create_requires_visitor_name_and_phone(self):
        """Visitor name + phone are system-required when no profile is linked."""
        now = int(time.time())
        with pytest.raises(ValidationError):
            AppointmentCreate(
                tenant_id="tenant123",
                host_id="host111",
                department_id="dept789",
                scheduled_datetime=now + 3600,
                visitor_name_snapshot="Jane Smith",
                # visitor_phone missing -> rejected
            )

    def test_appointment_create_visitor_identity_from_profile(self):
        """A linked visitor_profile_id exempts name/phone (profile supplies them)."""
        now = int(time.time())
        payload = AppointmentCreate(
            tenant_id="tenant123",
            host_id="host111",
            department_id="dept789",
            scheduled_datetime=now + 3600,
            visitor_profile_id="visitor456",
        )
        assert payload.visitor_profile_id == "visitor456"
        assert payload.visitor_phone is None

    def test_appointment_create_with_optional_fields(self):
        """Test AppointmentCreate with optional fields."""
        now = int(time.time())
        payload = AppointmentCreate(
            tenant_id="tenant123",
            host_id="host111",
            department_id="dept789",
            scheduled_datetime=now + 3600,
            visitor_profile_id="visitor456",
            visitor_name_snapshot="Jane Smith",
            host_name_snapshot="John Host",
            purpose="Business Meeting",
            created_by="admin123",
        )
        assert payload.visitor_profile_id == "visitor456"
        assert payload.visitor_name_snapshot == "Jane Smith"
        assert payload.purpose == "Business Meeting"
        assert payload.created_by == "admin123"

    def test_appointment_update_all_optional(self):
        """Test AppointmentUpdate makes all fields optional."""
        payload = AppointmentUpdate()
        assert payload.visitor_profile_id is None
        assert payload.status is None
        assert payload.scheduled_datetime is None
        assert isinstance(payload.last_updated, int)

    def test_appointment_update_partial(self):
        """Test AppointmentUpdate with partial updates."""
        now = int(time.time())
        payload = AppointmentUpdate(
            status=AppointmentStatus.FULFILLED, scheduled_datetime=now + 7200
        )
        assert payload.status == AppointmentStatus.FULFILLED
        assert payload.scheduled_datetime == now + 7200
        assert payload.visitor_profile_id is None

    def test_appointment_out_objectid_conversion(self):
        """Test AppointmentOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "host_id": "host111",
            "department_id": "dept789",
            "scheduled_datetime": now + 3600,
            "status": "scheduled",
            "date_created": now,
            "last_updated": now,
        }
        out = AppointmentOut(**data)
        assert out.id == str(oid)

    def test_appointment_enum_validation(self):
        """Test AppointmentCreate validates status enum."""
        now = int(time.time())
        with pytest.raises(ValidationError):
            AppointmentCreate(
                tenant_id="tenant123",
                host_id="host111",
                department_id="dept789",
                scheduled_datetime=now + 3600,
                status="invalid_status",
            )


# ============================================================================
# PRIVACY NOTICE SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestPrivacyNoticeSchema:
    def test_privacy_notice_create_required_fields(self):
        """Test PrivacyNoticeCreate with required fields."""
        payload = PrivacyNoticeCreate(
            tenant_id="tenant123",
            version_code="v1.0",
            title="Privacy Notice",
            summary="This is a privacy notice.",
        )
        assert payload.tenant_id == "tenant123"
        assert payload.version_code == "v1.0"
        assert payload.title == "Privacy Notice"
        assert payload.summary == "This is a privacy notice."
        assert payload.is_active is True
        assert isinstance(payload.date_created, int)

    def test_privacy_notice_create_with_optional_fields(self):
        """Test PrivacyNoticeCreate with optional fields."""
        now = int(time.time())
        payload = PrivacyNoticeCreate(
            tenant_id="tenant123",
            version_code="v2.0",
            title="Updated Privacy Notice",
            summary="Updated summary",
            full_policy_url="https://example.com/privacy",
            effective_from=now,
            effective_to=now + 86400,
            is_active=False,
        )
        assert payload.full_policy_url == "https://example.com/privacy"
        assert payload.effective_from == now
        assert payload.effective_to == now + 86400
        assert payload.is_active is False

    def test_privacy_notice_update_all_optional(self):
        """Test PrivacyNoticeUpdate makes all fields optional."""
        payload = PrivacyNoticeUpdate()
        assert payload.title is None
        assert payload.summary is None
        assert payload.is_active is None
        assert isinstance(payload.last_updated, int)

    def test_privacy_notice_update_partial(self):
        """Test PrivacyNoticeUpdate with partial updates."""
        now = int(time.time())
        payload = PrivacyNoticeUpdate(title="New Title", effective_to=now + 86400)
        assert payload.title == "New Title"
        assert payload.effective_to == now + 86400
        assert payload.summary is None

    def test_privacy_notice_out_objectid_conversion(self):
        """Test PrivacyNoticeOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "version_code": "v1.0",
            "title": "Privacy Notice",
            "summary": "Summary",
            "is_active": True,
            "date_created": now,
        }
        out = PrivacyNoticeOut(**data)
        assert out.id == str(oid)


# ============================================================================
# AUDIT LOG SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestAuditLogSchema:
    def test_audit_log_create_required_fields(self):
        """AuditLogCreate accepts the audit_trail document shape."""
        payload = AuditLogCreate(
            actor_id="user456",
            actor_role="admin",
            action="USER_LOGIN",
            resource_type="user",
            resource_id="user456",
        )
        assert payload.actor_id == "user456"
        assert payload.actor_role == "admin"
        assert payload.action == "USER_LOGIN"
        assert payload.resource_type == "user"
        assert payload.resource_id == "user456"
        assert payload.tenant_id is None
        assert payload.details == {}
        assert isinstance(payload.timestamp, int)

    def test_audit_log_create_with_optional_fields(self):
        """AuditLogCreate carries tenant_id, details, request_id when provided."""
        now = int(time.time())
        payload = AuditLogCreate(
            tenant_id="tenant123",
            actor_id="user456",
            actor_role="super_admin",
            action="VISITOR_CHECKED_IN",
            resource_type="visit_session",
            resource_id="session789",
            details={"ip": "192.168.1.1", "reason": "Scheduled visit"},
            request_id="req-xyz",
            timestamp=now,
        )
        assert payload.tenant_id == "tenant123"
        assert payload.details["ip"] == "192.168.1.1"
        assert payload.request_id == "req-xyz"
        assert payload.timestamp == now

    def test_audit_log_out_objectid_conversion(self):
        """AuditLogOut converts ObjectId to string."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "actor_id": "user456",
            "actor_role": "admin",
            "action": "USER_LOGIN",
            "resource_type": "user",
            "resource_id": "user456",
            "timestamp": now,
        }
        out = AuditLogOut(**data)
        assert out.id == str(oid)
        assert out.actor_role == "admin"
        assert out.resource_type == "user"


# ============================================================================
# INCIDENT LOG SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestIncidentLogSchema:
    def test_incident_log_create_required_fields(self):
        """Test IncidentLogCreate with required fields."""
        payload = IncidentLogCreate(
            tenant_id="tenant123",
            reported_by="admin123",
            incident_type=IncidentType.DATA_BREACH,
            description="Data breach detected in visitor profiles",
        )
        assert payload.tenant_id == "tenant123"
        assert payload.reported_by == "admin123"
        assert payload.incident_type == IncidentType.DATA_BREACH
        assert payload.description == "Data breach detected in visitor profiles"
        assert payload.status == IncidentStatus.OPEN
        assert payload.ndpc_notified is False
        assert isinstance(payload.date_created, int)

    def test_incident_log_create_with_optional_fields(self):
        """Test IncidentLogCreate with optional fields."""
        now = int(time.time())
        payload = IncidentLogCreate(
            tenant_id="tenant123",
            reported_by="admin123",
            incident_type=IncidentType.UNAUTHORIZED_ACCESS,
            description="Unauthorized access attempt",
            status=IncidentStatus.INVESTIGATING,
            risk_level="high",
            data_affected="visitor_id_images",
            mitigation_steps="Disabled API endpoint",
            ndpc_notified=True,
            ndpc_notified_at=now,
            detection_time=now,
        )
        assert payload.risk_level == "high"
        assert payload.data_affected == "visitor_id_images"
        assert payload.ndpc_notified is True
        assert payload.detection_time == now

    def test_incident_log_update_all_optional(self):
        """Test IncidentLogUpdate makes all fields optional."""
        payload = IncidentLogUpdate()
        assert payload.status is None
        assert payload.description is None
        assert payload.risk_level is None
        assert isinstance(payload.last_updated, int)

    def test_incident_log_update_partial(self):
        """Test IncidentLogUpdate with partial updates."""
        now = int(time.time())
        payload = IncidentLogUpdate(
            status=IncidentStatus.CONTAINED, risk_level="medium", ndpc_notified_at=now
        )
        assert payload.status == IncidentStatus.CONTAINED
        assert payload.risk_level == "medium"
        assert payload.description is None

    def test_incident_log_out_objectid_conversion(self):
        """Test IncidentLogOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "reported_by": "admin123",
            "incident_type": "data_breach",
            "status": "open",
            "description": "Breach detected",
            "ndpc_notified": False,
            "date_created": now,
        }
        out = IncidentLogOut(**data)
        assert out.id == str(oid)

    def test_incident_log_enum_validation(self):
        """Test IncidentLogCreate validates enum fields."""
        with pytest.raises(ValidationError):
            IncidentLogCreate(
                tenant_id="tenant123",
                reported_by="admin123",
                incident_type="invalid_type",
                description="Test",
            )
        with pytest.raises(ValidationError):
            IncidentLogCreate(
                tenant_id="tenant123",
                reported_by="admin123",
                incident_type=IncidentType.DATA_BREACH,
                description="Test",
                status="invalid_status",
            )


# ============================================================================
# DATA SUBJECT REQUEST SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestDSRSchema:
    def test_dsr_create_required_fields(self):
        """Test DSRCreate with required fields."""
        payload = DSRCreate(
            tenant_id="tenant123",
            visitor_profile_id="visitor456",
            request_type=DSRType.ACCESS,
        )
        assert payload.tenant_id == "tenant123"
        assert payload.visitor_profile_id == "visitor456"
        assert payload.request_type == DSRType.ACCESS
        assert payload.status == DSRStatus.PENDING
        assert payload.identity_verified is False
        assert isinstance(payload.received_at, int)
        assert isinstance(payload.date_created, int)

    def test_dsr_create_with_optional_fields(self):
        """Test DSRCreate with optional fields."""
        now = int(time.time())
        payload = DSRCreate(
            tenant_id="tenant123",
            visitor_profile_id="visitor456",
            admin_id="admin789",
            request_type=DSRType.DELETION,
            status=DSRStatus.IN_PROGRESS,
            identity_verified=True,
            sla_deadline=now + 2592000,  # 30 days
            notes="Visitor requested data deletion",
        )
        assert payload.admin_id == "admin789"
        assert payload.status == DSRStatus.IN_PROGRESS
        assert payload.identity_verified is True
        assert payload.notes == "Visitor requested data deletion"

    def test_dsr_update_all_optional(self):
        """Test DSRUpdate makes all fields optional."""
        payload = DSRUpdate()
        assert payload.admin_id is None
        assert payload.status is None
        assert payload.identity_verified is None
        assert isinstance(payload.last_updated, int)

    def test_dsr_update_partial(self):
        """Test DSRUpdate with partial updates."""
        now = int(time.time())
        payload = DSRUpdate(
            status=DSRStatus.COMPLETED, resolved_at=now, identity_verified=True
        )
        assert payload.status == DSRStatus.COMPLETED
        assert payload.resolved_at == now
        assert payload.admin_id is None

    def test_dsr_out_objectid_conversion(self):
        """Test DSROut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "visitor_profile_id": "visitor456",
            "request_type": "access",
            "status": "pending",
            "identity_verified": False,
            "received_at": now,
            "date_created": now,
        }
        out = DSROut(**data)
        assert out.id == str(oid)

    def test_dsr_out_carries_external_id_summaries(self):
        """DSROut exposes admin_summary + visit_session_summary companions and
        the access-export artifact fields (External ID Summary Fields rule)."""
        out = DSROut(
            _id=ObjectId(),
            tenant_id="t",
            visitor_profile_id="v",
            request_type="access",
            status="pending",
        )
        dumped = out.model_dump()
        assert "visitor_profile_summary" in dumped
        assert "admin_summary" in dumped
        assert "visit_session_summary" in dumped
        assert "access_export_object_key" in dumped
        assert "access_export_emailed_to" in dumped

    def test_dsr_update_accepts_access_export_fields(self):
        """The writer stamps the access-export artifact via DSRUpdate."""
        upd = DSRUpdate(
            status="completed",
            access_export_object_key="dsr-access-exports/t/d/abc.zip",
            access_export_expires_at=123,
            access_export_emailed_to="ada@example.com",
            access_export_generated_at=100,
        )
        assert upd.access_export_emailed_to == "ada@example.com"
        assert upd.access_export_object_key.endswith(".zip")

    def test_dsr_correction_request_allowlist(self):
        """DSRCorrectionRequest carries only the four correctable PII fields."""
        from schemas.data_subject_request_schema import DSRCorrectionRequest

        req = DSRCorrectionRequest(full_name="New Name", company="New Co")
        dumped = req.model_dump(exclude_none=True)
        assert dumped == {"full_name": "New Name", "company": "New Co"}
        assert set(DSRCorrectionRequest.model_fields) == {
            "full_name",
            "phone",
            "email_address",
            "company",
        }

    def test_dsr_enum_validation(self):
        """Test DSRCreate validates enum fields."""
        with pytest.raises(ValidationError):
            DSRCreate(
                tenant_id="tenant123",
                visitor_profile_id="visitor456",
                request_type="invalid_type",
            )
        with pytest.raises(ValidationError):
            DSRCreate(
                tenant_id="tenant123",
                visitor_profile_id="visitor456",
                request_type=DSRType.ACCESS,
                status="invalid_status",
            )


# ============================================================================
# RETENTION POLICY SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestRetentionPolicySchema:
    def test_retention_policy_create_required_fields(self):
        """Test RetentionPolicyCreate with required fields."""
        payload = RetentionPolicyCreate(
            tenant_id="tenant123", scope="visit_sessions", retention_days=1095
        )
        assert payload.tenant_id == "tenant123"
        assert payload.scope == "visit_sessions"
        assert payload.retention_days == 1095
        assert payload.action == DeletionAction.ANONYMISE
        assert isinstance(payload.date_created, int)

    def test_retention_policy_create_with_delete_action(self):
        """Test RetentionPolicyCreate with DELETE action."""
        payload = RetentionPolicyCreate(
            tenant_id="tenant123",
            scope="id_images",
            retention_days=365,
            action=DeletionAction.DELETE,
        )
        assert payload.action == DeletionAction.DELETE

    def test_retention_policy_update_all_optional(self):
        """Test RetentionPolicyUpdate makes all fields optional."""
        payload = RetentionPolicyUpdate()
        assert payload.retention_days is None
        assert payload.action is None
        assert isinstance(payload.last_updated, int)

    def test_retention_policy_update_partial(self):
        """Test RetentionPolicyUpdate with partial updates."""
        payload = RetentionPolicyUpdate(
            retention_days=730, action=DeletionAction.DELETE
        )
        assert payload.retention_days == 730
        assert payload.action == DeletionAction.DELETE

    def test_retention_policy_out_objectid_conversion(self):
        """Test RetentionPolicyOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "scope": "visit_sessions",
            "retention_days": 1095,
            "action": "anonymise",
            "date_created": now,
        }
        out = RetentionPolicyOut(**data)
        assert out.id == str(oid)

    def test_retention_policy_enum_validation(self):
        """Test RetentionPolicyCreate validates action enum."""
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(
                tenant_id="tenant123",
                scope="visit_sessions",
                retention_days=1095,
                action="invalid_action",
            )


# ============================================================================
# SUB PROCESSOR SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestSubProcessorSchema:
    def test_sub_processor_create_required_fields(self):
        """Test SubProcessorCreate with required fields."""
        payload = SubProcessorCreate(
            tenant_id="tenant123", provider="AWS", purpose="Cloud storage"
        )
        assert payload.tenant_id == "tenant123"
        assert payload.provider == "AWS"
        assert payload.purpose == "Cloud storage"
        assert payload.dpa_signed is False
        assert payload.uses_data_for_training is False
        assert isinstance(payload.date_created, int)

    def test_sub_processor_create_with_optional_fields(self):
        """Test SubProcessorCreate with optional fields."""
        payload = SubProcessorCreate(
            tenant_id="tenant123",
            provider="Google Cloud",
            purpose="Email service",
            jurisdiction="US",
            dpa_signed=True,
            uses_data_for_training=False,
        )
        assert payload.jurisdiction == "US"
        assert payload.dpa_signed is True
        assert payload.uses_data_for_training is False

    def test_sub_processor_update_all_optional(self):
        """Test SubProcessorUpdate makes all fields optional."""
        payload = SubProcessorUpdate()
        assert payload.provider is None
        assert payload.purpose is None
        assert payload.jurisdiction is None
        assert isinstance(payload.last_updated, int)

    def test_sub_processor_update_partial(self):
        """Test SubProcessorUpdate with partial updates."""
        payload = SubProcessorUpdate(jurisdiction="EU", dpa_signed=True)
        assert payload.jurisdiction == "EU"
        assert payload.dpa_signed is True
        assert payload.provider is None

    def test_sub_processor_out_objectid_conversion(self):
        """Test SubProcessorOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "provider": "AWS",
            "purpose": "Cloud storage",
            "dpa_signed": False,
            "uses_data_for_training": False,
            "date_created": now,
        }
        out = SubProcessorOut(**data)
        assert out.id == str(oid)


# ============================================================================
# DELETION LOG SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestDeletionLogSchema:
    def test_deletion_log_create_required_fields(self):
        """Test DeletionLogCreate with required fields."""
        payload = DeletionLogCreate(
            tenant_id="tenant123",
            entity_type="VisitorProfile",
            entity_id="visitor456",
            reason="Data subject request",
            action=DeletionAction.DELETE,
        )
        assert payload.tenant_id == "tenant123"
        assert payload.entity_type == "VisitorProfile"
        assert payload.entity_id == "visitor456"
        assert payload.reason == "Data subject request"
        assert payload.action == DeletionAction.DELETE
        assert isinstance(payload.timestamp, int)

    def test_deletion_log_create_with_optional_fields(self):
        """Test DeletionLogCreate with optional fields."""
        payload = DeletionLogCreate(
            tenant_id="tenant123",
            entity_type="VisitSession",
            entity_id="session789",
            reason="Retention policy",
            action=DeletionAction.ANONYMISE,
            performed_by="admin123",
        )
        assert payload.performed_by == "admin123"
        assert payload.action == DeletionAction.ANONYMISE

    def test_deletion_log_out_objectid_conversion(self):
        """Test DeletionLogOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "tenant_id": "tenant123",
            "entity_type": "VisitorProfile",
            "entity_id": "visitor456",
            "reason": "Data subject request",
            "action": "delete",
            "timestamp": now,
        }
        out = DeletionLogOut(**data)
        assert out.id == str(oid)

    def test_deletion_log_enum_validation(self):
        """Test DeletionLogCreate validates action enum."""
        with pytest.raises(ValidationError):
            DeletionLogCreate(
                tenant_id="tenant123",
                entity_type="VisitorProfile",
                entity_id="visitor456",
                reason="Test",
                action="invalid_action",
            )


# ============================================================================
# USER SESSION SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestUserSessionSchema:
    def test_user_session_create_required_fields(self):
        """Test UserSessionCreate with required fields."""
        payload = UserSessionCreate(user_id="user123", tenant_id="tenant456")
        assert payload.user_id == "user123"
        assert payload.tenant_id == "tenant456"
        assert payload.mfa_passed is False
        assert payload.ip_address is None
        assert isinstance(payload.started_at, int)
        assert isinstance(payload.last_activity_at, int)

    def test_user_session_create_with_optional_fields(self):
        """Test UserSessionCreate with optional fields."""
        now = int(time.time())
        payload = UserSessionCreate(
            user_id="user123",
            tenant_id="tenant456",
            ip_address="192.168.1.1",
            device_signature="device_sig_123",
            user_agent="Mozilla/5.0...",
            mfa_passed=True,
            expires_at=now + 3600,
        )
        assert payload.ip_address == "192.168.1.1"
        assert payload.device_signature == "device_sig_123"
        assert payload.mfa_passed is True
        assert payload.expires_at == now + 3600

    def test_user_session_create_auto_timestamps(self):
        """Test UserSessionCreate generates timestamps."""
        before = int(time.time())
        payload = UserSessionCreate(user_id="user123", tenant_id="tenant456")
        after = int(time.time())
        assert before <= payload.started_at <= after
        assert before <= payload.last_activity_at <= after

    def test_user_session_update_all_optional(self):
        """Test UserSessionUpdate makes all fields optional."""
        payload = UserSessionUpdate()
        assert payload.last_activity_at is None
        assert payload.ended_at is None

    def test_user_session_update_partial(self):
        """Test UserSessionUpdate with partial updates."""
        now = int(time.time())
        payload = UserSessionUpdate(last_activity_at=now, ended_at=now + 100)
        assert payload.last_activity_at == now
        assert payload.ended_at == now + 100

    def test_user_session_out_objectid_conversion(self):
        """Test UserSessionOut converts ObjectId."""
        oid = ObjectId()
        now = int(time.time())
        data = {
            "_id": oid,
            "user_id": "user123",
            "tenant_id": "tenant456",
            "mfa_passed": False,
            "started_at": now,
            "last_activity_at": now,
        }
        out = UserSessionOut(**data)
        assert out.id == str(oid)

    def test_user_session_out_populate_by_name(self):
        """Test UserSessionOut accepts id field."""
        oid = str(ObjectId())
        now = int(time.time())
        data = {
            "id": oid,
            "user_id": "user123",
            "tenant_id": "tenant456",
            "mfa_passed": False,
            "started_at": now,
            "last_activity_at": now,
        }
        out = UserSessionOut(**data)
        assert out.id == oid


# ============================================================================
# TENANT BOOTSTRAP SCHEMA TESTS
# ============================================================================


@pytest.mark.unit
class TestTenantBootstrapSchema:
    def test_bootstrap_request_with_required_fields(self):
        """Test TenantBootstrapRequest with all required fields."""
        payload = TenantBootstrapRequest(
            company_name="Acme Corp",
            admin_full_name="Jane Doe",
            admin_email="jane@acmecorp.com",
            admin_password="SecurePass123!",
        )
        assert payload.company_name == "Acme Corp"
        assert payload.admin_full_name == "Jane Doe"
        assert payload.admin_email == "jane@acmecorp.com"
        assert payload.admin_password == "SecurePass123!"
        # Defaults
        assert payload.lawful_basis == LawfulBasis.LEGITIMATE_INTEREST
        assert payload.notice_display_mode == NoticeDisplayMode.PASSIVE
        assert payload.retention_days == 1095
        assert payload.default_retention_action == DeletionAction.ANONYMISE
        assert payload.cross_border_approved is False

    def test_bootstrap_request_with_all_tenant_fields(self):
        """Test TenantBootstrapRequest with all optional tenant fields."""
        payload = TenantBootstrapRequest(
            company_name="Tech Inc",
            lawful_basis=LawfulBasis.CONSENT,
            notice_display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
            retention_days=365,
            default_retention_action=DeletionAction.DELETE,
            dpo_contact_email="dpo@techinc.com",
            privacy_policy_url="https://techinc.com/privacy",
            country_of_hosting="Nigeria",
            cross_border_approved=True,
            admin_full_name="John Smith",
            admin_email="john@techinc.com",
            admin_password="AnotherPass456!",
        )
        assert payload.company_name == "Tech Inc"
        assert payload.lawful_basis == LawfulBasis.CONSENT
        assert payload.retention_days == 365
        assert payload.dpo_contact_email == "dpo@techinc.com"
        assert payload.country_of_hosting == "Nigeria"
        assert payload.cross_border_approved is True

    def test_bootstrap_request_rejects_missing_company_name(self):
        """Test TenantBootstrapRequest fails without company_name."""
        with pytest.raises(ValidationError):
            TenantBootstrapRequest(
                admin_full_name="Jane Doe",
                admin_email="jane@example.com",
                admin_password="Pass123!",
            )

    def test_bootstrap_request_rejects_missing_admin_email(self):
        """Test TenantBootstrapRequest fails without admin_email."""
        with pytest.raises(ValidationError):
            TenantBootstrapRequest(
                company_name="Acme Corp",
                admin_full_name="Jane Doe",
                admin_password="Pass123!",
            )

    def test_bootstrap_request_rejects_invalid_admin_email(self):
        """Test TenantBootstrapRequest fails with invalid email format."""
        with pytest.raises(ValidationError):
            TenantBootstrapRequest(
                company_name="Acme Corp",
                admin_full_name="Jane Doe",
                admin_email="not-an-email",
                admin_password="Pass123!",
            )

    def test_bootstrap_request_rejects_missing_admin_password(self):
        """Test TenantBootstrapRequest fails without admin_password."""
        with pytest.raises(ValidationError):
            TenantBootstrapRequest(
                company_name="Acme Corp",
                admin_full_name="Jane Doe",
                admin_email="jane@example.com",
            )

    def test_bootstrap_request_rejects_missing_admin_name(self):
        """Test TenantBootstrapRequest fails without admin_full_name."""
        with pytest.raises(ValidationError):
            TenantBootstrapRequest(
                company_name="Acme Corp",
                admin_email="jane@example.com",
                admin_password="Pass123!",
            )


# ============================================================================
# LOGIN PASSWORD WHITESPACE STRIPPING TESTS
# ============================================================================


@pytest.mark.unit
class TestLoginPasswordWhitespaceStripping:
    """Login schemas must strip leading/trailing whitespace from the password
    field. Pasted passwords frequently carry stray spaces or newlines that the
    user never intended to type — those should not silently break authentication.
    """

    def test_admin_login_strips_leading_and_trailing_whitespace(self):
        payload = AdminLogin(email="admin@example.com", password="  MyP@ssw0rd!  ")
        assert payload.password == "MyP@ssw0rd!"

    def test_admin_login_strips_tabs_and_newlines(self):
        payload = AdminLogin(email="admin@example.com", password="\tMyP@ssw0rd!\n")
        assert payload.password == "MyP@ssw0rd!"

    def test_admin_login_preserves_internal_whitespace(self):
        payload = AdminLogin(email="admin@example.com", password="  My P@ss w0rd!  ")
        assert payload.password == "My P@ss w0rd!"

    def test_admin_login_no_op_when_no_whitespace(self):
        payload = AdminLogin(email="admin@example.com", password="MyP@ssw0rd!")
        assert payload.password == "MyP@ssw0rd!"

    def test_system_user_login_strips_whitespace(self):
        payload = SystemUserLogin(email="user@example.com", password="  Str0ng#Pass!  ")
        assert payload.password == "Str0ng#Pass!"

    def test_system_user_tenant_login_strips_whitespace(self):
        payload = SystemUserTenantLogin(
            email="user@example.com", password="\n Str0ng#Pass! \t"
        )
        assert payload.password == "Str0ng#Pass!"

    def test_user_login_strips_whitespace(self):
        payload = UserLogin(email="user@example.com", password=" Str0ng#Pass! ")
        assert payload.password == "Str0ng#Pass!"

    def test_user_login_preserves_internal_whitespace(self):
        payload = UserLogin(email="user@example.com", password=" pass with spaces ")
        assert payload.password == "pass with spaces"
