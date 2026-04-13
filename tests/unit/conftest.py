from __future__ import annotations

import time
from typing import Callable

import pytest
from bson import ObjectId

from schemas.tenant_schema import TenantCreate
from schemas.department_schema import DepartmentCreate
from schemas.system_user_schema import SystemUserCreate
from schemas.visitor_profile_schema import VisitorProfileCreate
from schemas.visit_session_schema import VisitSessionCreate
from schemas.appointment_schema import AppointmentCreate
from schemas.privacy_notice_schema import PrivacyNoticeCreate
from schemas.incident_log_schema import IncidentLogCreate
from schemas.audit_log_schema import AuditLogCreate
from schemas.data_subject_request_schema import DSRCreate
from schemas.retention_policy_schema import RetentionPolicyCreate
from schemas.sub_processor_schema import SubProcessorCreate
from schemas.deletion_log_schema import DeletionLogCreate
from schemas.user_session_schema import UserSessionCreate
from schemas.imports import (
    LawfulBasis,
    NoticeDisplayMode,
    DeletionAction,
    SystemUserRole,
    VisitStatus,
    CheckInMethod,
    VerificationStatus,
    AppointmentStatus,
    IncidentType,
    IncidentStatus,
    DSRType,
    DSRStatus,
    ProfilingPreference,
)


# --- Tenant Factory ---


@pytest.fixture
def tenant_factory() -> Callable[..., TenantCreate]:
    def _make(**kwargs) -> TenantCreate:
        defaults: dict = {
            "company_name": "Test Company Inc.",
            "lawful_basis": LawfulBasis.LEGITIMATE_INTEREST,
            "notice_display_mode": NoticeDisplayMode.PASSIVE,
            "retention_days": 1095,
            "default_retention_action": DeletionAction.ANONYMISE,
            "dpo_contact_email": "dpo@testcompany.com",
            "privacy_policy_url": "https://testcompany.com/privacy",
            "country_of_hosting": "Nigeria",
            "cross_border_approved": False,
            "is_active": True,
            "active_notice_version": None,
        }
        defaults.update(kwargs)
        return TenantCreate(**defaults)

    return _make


# --- Department Factory ---


@pytest.fixture
def department_factory() -> Callable[..., DepartmentCreate]:
    def _make(**kwargs) -> DepartmentCreate:
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "code": f"DEPT-{ObjectId()}",
            "name": "Engineering Department",
            "is_active": True,
            "created_by": str(ObjectId()),
        }
        defaults.update(kwargs)
        return DepartmentCreate(**defaults)

    return _make


# --- SystemUser Factory ---


@pytest.fixture
def system_user_factory() -> Callable[..., SystemUserCreate]:
    def _make(**kwargs) -> SystemUserCreate:
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "department_id": str(ObjectId()),
            "full_name": "John Doe",
            "email": f"user+{ObjectId()}@testcompany.com",
            "role": SystemUserRole.RECEPTIONIST,
            "account_status": "ACTIVE",
            "is_active": True,
            "password_hash": "plaintext-password-123",
        }
        defaults.update(kwargs)
        return SystemUserCreate(**defaults)

    return _make


# --- VisitorProfile Factory ---


@pytest.fixture
def visitor_profile_factory() -> Callable[..., VisitorProfileCreate]:
    def _make(**kwargs) -> VisitorProfileCreate:
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "phone": "+2348012345678",
            "email_address": f"visitor+{ObjectId()}@example.com",
            "full_name": "Jane Smith",
            "company": "Acme Corp",
            "photo_object_key": None,
            "id_type": "passport",
            "id_number": "A12345678",
            "id_image_object_key": None,
            "profiling_preference": ProfilingPreference.ALLOWED,
            "last_verification_date": None,
        }
        defaults.update(kwargs)
        return VisitorProfileCreate(**defaults)

    return _make


# --- VisitSession Factory ---


@pytest.fixture
def visit_session_factory() -> Callable[..., VisitSessionCreate]:
    def _make(**kwargs) -> VisitSessionCreate:
        int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "visitor_profile_id": str(ObjectId()),
            "department_id": str(ObjectId()),
            "host_id": str(ObjectId()),
            "receptionist_id": str(ObjectId()),
            "appointment_id": None,
            "privacy_notice_version_id": str(ObjectId()),
            "check_in_method": CheckInMethod.MANUAL,
            "check_out_method": None,
            "verification_status": VerificationStatus.UNVERIFIED,
            "verification_method": None,
            "verified_by": None,
            "status": VisitStatus.REGISTERED,
            "purpose": "Business meeting",
            "visitor_name_snapshot": "Jane Smith",
            "company_snapshot": "Acme Corp",
            "host_name_snapshot": "John Doe",
            "department_name_snapshot": "Engineering",
            "receptionist_name_snapshot": "Alice Brown",
            "consent_notice_displayed": False,
            "consent_granted": None,
            "consent_method": None,
            "consent_timestamp": None,
            "consent_captured_by_user_id": None,
            "consent_withdrawal_at": None,
            "lawful_basis_at_time": LawfulBasis.LEGITIMATE_INTEREST,
            "badge_qr_token": None,
            "badge_format": None,
            "badge_generation_time": None,
            "badge_expiry": None,
            "badge_pdf_object_key": None,
        }
        defaults.update(kwargs)
        return VisitSessionCreate(**defaults)

    return _make


# --- Appointment Factory ---


@pytest.fixture
def appointment_factory() -> Callable[..., AppointmentCreate]:
    def _make(**kwargs) -> AppointmentCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "visitor_profile_id": str(ObjectId()),
            "host_id": str(ObjectId()),
            "department_id": str(ObjectId()),
            "visitor_name_snapshot": "Jane Smith",
            "host_name_snapshot": "John Doe",
            "scheduled_datetime": now + 3600,
            "purpose": "Project Discussion",
            "status": AppointmentStatus.SCHEDULED,
            "created_by": str(ObjectId()),
        }
        defaults.update(kwargs)
        return AppointmentCreate(**defaults)

    return _make


# --- PrivacyNotice Factory ---


@pytest.fixture
def privacy_notice_factory() -> Callable[..., PrivacyNoticeCreate]:
    def _make(**kwargs) -> PrivacyNoticeCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "version_code": f"v1.0.{ObjectId()}",
            "title": "Privacy Notice for Visitors",
            "summary": "This is a summary of our privacy practices",
            "full_policy_url": "https://testcompany.com/privacy/full",
            "effective_from": now,
            "effective_to": None,
            "is_active": True,
        }
        defaults.update(kwargs)
        return PrivacyNoticeCreate(**defaults)

    return _make


# --- IncidentLog Factory ---


@pytest.fixture
def incident_log_factory() -> Callable[..., IncidentLogCreate]:
    def _make(**kwargs) -> IncidentLogCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "reported_by": str(ObjectId()),
            "incident_type": IncidentType.DATA_BREACH,
            "status": IncidentStatus.OPEN,
            "description": "Unauthorized access to visitor data",
            "risk_level": "high",
            "data_affected": "visitor_profiles",
            "mitigation_steps": "Revoked access, notified affected parties",
            "ndpc_notified": False,
            "ndpc_notified_at": None,
            "detection_time": now,
        }
        defaults.update(kwargs)
        return IncidentLogCreate(**defaults)

    return _make


# --- AuditLog Factory ---


@pytest.fixture
def audit_log_factory() -> Callable[..., AuditLogCreate]:
    def _make(**kwargs) -> AuditLogCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "actor_id": str(ObjectId()),
            "actor_name_snapshot": "Admin User",
            "user_session_id": str(ObjectId()),
            "action": "visitor_checked_in",
            "target_entity": "visit_session",
            "target_id": str(ObjectId()),
            "ip": "192.168.1.100",
            "device_signature": "Device-XYZ",
            "reason": "Standard check-in procedure",
            "occurred_at": now,
        }
        defaults.update(kwargs)
        return AuditLogCreate(**defaults)

    return _make


# --- DSR (Data Subject Request) Factory ---


@pytest.fixture
def dsr_factory() -> Callable[..., DSRCreate]:
    def _make(**kwargs) -> DSRCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "visitor_profile_id": str(ObjectId()),
            "admin_id": None,
            "visit_session_id": None,
            "request_type": DSRType.ACCESS,
            "status": DSRStatus.PENDING,
            "identity_verified": False,
            "sla_deadline": now + (30 * 86400),
            "notes": "Access request for personal data",
        }
        defaults.update(kwargs)
        return DSRCreate(**defaults)

    return _make


# --- RetentionPolicy Factory ---


@pytest.fixture
def retention_policy_factory() -> Callable[..., RetentionPolicyCreate]:
    def _make(**kwargs) -> RetentionPolicyCreate:
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "scope": "visit_sessions",
            "retention_days": 1095,
            "action": DeletionAction.ANONYMISE,
        }
        defaults.update(kwargs)
        return RetentionPolicyCreate(**defaults)

    return _make


# --- SubProcessor Factory ---


@pytest.fixture
def sub_processor_factory() -> Callable[..., SubProcessorCreate]:
    def _make(**kwargs) -> SubProcessorCreate:
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "provider": "Amazon Web Services",
            "purpose": "Cloud storage and data processing",
            "jurisdiction": "US",
            "dpa_signed": True,
            "uses_data_for_training": False,
        }
        defaults.update(kwargs)
        return SubProcessorCreate(**defaults)

    return _make


# --- DeletionLog Factory ---


@pytest.fixture
def deletion_log_factory() -> Callable[..., DeletionLogCreate]:
    def _make(**kwargs) -> DeletionLogCreate:
        now = int(time.time())
        defaults: dict = {
            "tenant_id": str(ObjectId()),
            "entity_type": "visitor_profile",
            "entity_id": str(ObjectId()),
            "reason": "Retention period expired",
            "action": DeletionAction.ANONYMISE,
            "performed_by": str(ObjectId()),
            "timestamp": now,
        }
        defaults.update(kwargs)
        return DeletionLogCreate(**defaults)

    return _make


# --- UserSession Factory ---


@pytest.fixture
def user_session_factory() -> Callable[..., UserSessionCreate]:
    def _make(**kwargs) -> UserSessionCreate:
        now = int(time.time())
        defaults: dict = {
            "user_id": str(ObjectId()),
            "tenant_id": str(ObjectId()),
            "ip_address": "192.168.1.100",
            "device_signature": "Device-ABC123",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "mfa_passed": False,
            "expires_at": now + 3600,
        }
        defaults.update(kwargs)
        return UserSessionCreate(**defaults)

    return _make
