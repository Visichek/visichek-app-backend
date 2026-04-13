from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


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
    SCHEDULED = "scheduled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
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
