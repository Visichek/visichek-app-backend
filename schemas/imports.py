import time  # noqa: F401  re-exported for `from schemas.imports import *`
from datetime import datetime, timezone  # noqa: F401
from pydantic import BaseModel, Field, EmailStr, model_validator  # noqa: F401
from typing import Any, Optional, List  # noqa: F401
from enum import Enum
from bson import ObjectId  # noqa: F401


class LoginType(str, Enum):
    google = "GOOGLE"
    email = "EMAIL"


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


class Permission(BaseModel):
    name: str
    methods: List[str]
    path: str
    key: Optional[str] = None
    description: Optional[str] = None


class PermissionList(BaseModel):
    permissions: List[Permission]


# --- VisiChek Domain Enums ---


class SystemUserRole(str, Enum):
    RECEPTIONIST = "receptionist"
    DEPT_ADMIN = "dept_admin"
    SUPER_ADMIN = "super_admin"
    AUDITOR = "auditor"
    SECURITY_OFFICER = "security_officer"
    DPO = "dpo"


class VisitStatus(str, Enum):
    REGISTERED = "registered"
    PENDING_VERIFICATION = "pending_verification"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    DENIED = "denied"
    CANCELLED = "cancelled"


class CheckInMethod(str, Enum):
    QR = "qr_registration"
    ID_SCAN = "id_scan"
    MANUAL = "manual_entry"


class CheckOutMethod(str, Enum):
    QR_SCAN = "qr_scan"
    MANUAL = "manual"


class VerificationMethod(str, Enum):
    ID_SCAN = "id_scan"
    QR_UPLOAD = "qr_upload"
    HOST_APPROVAL = "host_approval"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DENIED = "denied"


class AppointmentStatus(str, Enum):
    """Lifecycle of an expected appointment.

    Transitions are driven by the linked visit-session events:

    ``scheduled`` (initial)
        ↓ visitor presents at reception and gets a badge issued
    ``checked_in``
        ↓ visitor checks out (manually or via QR)
    ``checked_out``         ← terminal, happy path

    Other terminal states:

    * ``cancelled`` — manually cancelled by host/super_admin, or visitor
      was denied entry on arrival.
    * ``no_show`` — scheduled time passed without the visitor ever
      checking in (set by the periodic sweeper in
      ``services.appointment_lifecycle_service``).
    * ``missed`` — legacy alias kept for backwards compatibility with the
      old "missed" terminology; no new code should set this.
    * ``fulfilled`` — legacy alias of ``checked_out``. Older clients and
      tests still emit / expect this; the lifecycle helper treats it as
      equivalent and the dashboard rolls it into the same bucket.
    """

    SCHEDULED = "scheduled"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"
    # ── Legacy ──
    FULFILLED = "fulfilled"
    MISSED = "missed"


class LawfulBasis(str, Enum):
    CONSENT = "consent"
    LEGITIMATE_INTEREST = "legitimate_interest"


class DeletionAction(str, Enum):
    DELETE = "delete"
    ANONYMISE = "anonymise"


class DSRType(str, Enum):
    ACCESS = "access"
    CORRECTION = "correction"
    DELETION = "deletion"
    CONSENT_WITHDRAWAL = "consent_withdrawal"


class DSRStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class IncidentType(str, Enum):
    DATA_BREACH = "data_breach"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    DATA_EXPORT_EXPOSURE = "data_export_exposure"
    DEVICE_LOSS = "device_loss"
    MISCONFIGURATION = "misconfiguration"
    THIRD_PARTY = "third_party"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    CONTAINED = "contained"
    REPORTED_TO_NDPC = "reported_to_ndpc"
    CLOSED = "closed"


class BadgeFormat(str, Enum):
    A6 = "A6"
    A7 = "A7"


class NoticeDisplayMode(str, Enum):
    PASSIVE = "passive"
    ACTIVE_CONSENT = "active_consent"


class ProfilingPreference(str, Enum):
    ALLOWED = "allowed"
    OPTED_OUT = "opted_out"


class LogoPosition(str, Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER = "center"


# --- Check-In Domain Enums ---


class CheckinState(str, Enum):
    # PENDING_VERIFICATION: visitor submitted, KYC widget is running.
    # The check-in is invisible to receptionists in this state — only
    # after the provider webhook lands (or the visitor explicitly skips
    # KYC) does it transition to PENDING_APPROVAL and surface in the
    # approval queue.
    PENDING_VERIFICATION = "pending_verification"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHECKED_OUT = "checked_out"


class IDType(str, Enum):
    PASSPORT = "passport"
    DRIVERS_LICENSE = "drivers_license"
    NATIONAL_ID = "national_id"


class IDExtractionProvider(str, Enum):
    GOOGLE_DOCUMENT_AI = "google_document_ai"


class CheckinFieldCategory(str, Enum):
    BIO = "bio"
    TENANT_SPECIFIC = "tenant_specific"


class TenantEnumKind(str, Enum):
    """Kinds of tenant-configurable enumerations.

    Each kind is a list of accepted values the tenant offers to visitors
    on the kiosk form. The system ships defaults for each kind on tenant
    bootstrap; tenants can add, edit, or deactivate values via
    ``/v1/tenants/{tenant_id}/enums``.

    Add a new kind only when both the kiosk *and* the back-office UI
    need a per-tenant picker — every kind is loaded into the public
    kiosk payload, so this list should stay small.
    """

    PURPOSE_OF_VISIT = "purpose_of_visit"
    ID_TYPE = "id_type"
    VISITOR_CATEGORY = "visitor_category"


class KYCStatus(str, Enum):
    """Lifecycle of a KYC verification attempt for a single check-in.

    PENDING is the moment a kiosk submit decides to run KYC; ONGOING
    is once the widget has launched. SUCCESS / FAILED are the terminal
    states returned by the provider webhook. SKIPPED is set when the
    visitor explicitly declines verification on a tenant where KYC is
    optional.
    """

    PENDING = "pending"
    ONGOING = "ongoing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    EXPIRED = "expired"


class BadgeValidationReason(str, Enum):
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    REVOKED = "revoked"


# --- Support Case (platform-support thread between tenant and app admin) ---


class SupportCaseStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    AWAITING_TENANT = "awaiting_tenant"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


class SupportCasePriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SupportCaseCategory(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    FEATURE_REQUEST = "feature_request"
    DATA_PRIVACY = "data_privacy"
    OTHER = "other"


class SupportCaseAuthorType(str, Enum):
    TENANT = "tenant"
    ADMIN = "admin"
    SYSTEM = "system"


class SupportTier(str, Enum):
    """Per-plan support tier — controls admin alerting only.

    Tenants always receive customer emails at every state change regardless
    of tier. Admins are only paged for plans with STANDARD or PRIORITY.
    """

    NONE = "none"
    STANDARD = "standard"
    PRIORITY = "priority"


# --- Self-Onboarding (public marketing-site lead form) ---


class OnboardingStatus(str, Enum):
    """Lifecycle of a marketing-site onboarding submission."""

    NEW = "new"
    REJECTED = "rejected"
    # Admin accepted in full — tenant + super_admin created, no missing data.
    ACCEPTED = "accepted"
    # Admin accepted partially — tenant + super_admin created but the tenant
    # owes some additional info via /v1/onboarding/me/complete before they can
    # use the platform fully.
    PARTIAL_ACCEPTED = "partial_accepted"
    # Tenant filled in the requested missing fields — terminal happy path.
    COMPLETED = "completed"
    ARCHIVED = "archived"


# --- Tenant Form Builder ---


class FormTargetType(str, Enum):
    """What user-facing flow a tenant form attaches to."""

    APPOINTMENT = "appointment"
    CHECKIN = "checkin"
    VISIT_SESSION = "visit_session"


class FormStatus(str, Enum):
    """Lifecycle of a tenant form row.

    ``active``      — the live published version for this (tenant, target).
    ``archived``    — retired by super_admin; submissions still resolve.
    ``superseded``  — older published version, kept so historical
                      submissions remain interpretable.
    ``draft``       — only-draft row that has never been published.
    """

    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    DRAFT = "draft"


class FormFieldType(str, Enum):
    """Supported field types on a tenant form definition."""

    TEXT = "text"
    LONG_TEXT = "long_text"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    COUNTRY = "country"
    ADDRESS = "address"
    FILE = "file"
    IMAGE = "image"
    SIGNATURE = "signature"
    CONSENT_CHECKBOX = "consent_checkbox"
    RATING = "rating"
    ID_DOCUMENT = "id_document"
    HOST_PICKER = "host_picker"
    VISITOR_PICKER = "visitor_picker"
    CALCULATED = "calculated"
