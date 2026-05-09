from schemas.imports import *
from pydantic import Field
import time


class VisitSessionBase(BaseModel):
    tenant_id: str
    visitor_profile_id: str
    department_id: str
    host_id: Optional[str] = None
    receptionist_id: Optional[str] = None
    appointment_id: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None
    # Check-in details
    check_in_method: Optional[CheckInMethod] = None
    check_out_method: Optional[CheckOutMethod] = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verification_method: Optional[VerificationMethod] = None
    verified_by: Optional[str] = None
    status: VisitStatus = VisitStatus.REGISTERED
    purpose: Optional[str] = None
    # Name snapshots for historical accuracy
    visitor_name_snapshot: Optional[str] = None
    company_snapshot: Optional[str] = None
    host_name_snapshot: Optional[str] = None
    department_name_snapshot: Optional[str] = None
    receptionist_name_snapshot: Optional[str] = None
    # Consent fields (NDPA compliance)
    consent_notice_displayed: bool = False
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    consent_timestamp: Optional[int] = None
    consent_captured_by_user_id: Optional[str] = None
    consent_withdrawal_at: Optional[int] = None
    lawful_basis_at_time: Optional[LawfulBasis] = None
    # Badge fields
    badge_qr_token: Optional[str] = None
    badge_format: Optional[BadgeFormat] = None
    badge_generation_time: Optional[int] = None
    badge_expiry: Optional[int] = None
    badge_pdf_object_key: Optional[str] = None
    # Denial fields
    denial_reason: Optional[str] = None
    denied_by: Optional[str] = None


class VisitSessionCreate(VisitSessionBase):
    check_in_time: int = Field(default_factory=lambda: int(time.time()))
    date_created: int = Field(default_factory=lambda: int(time.time()))


class VisitSessionUpdate(BaseModel):
    status: Optional[VisitStatus] = None
    check_out_method: Optional[CheckOutMethod] = None
    check_out_time: Optional[int] = None
    verification_status: Optional[VerificationStatus] = None
    verification_method: Optional[VerificationMethod] = None
    verified_by: Optional[str] = None
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    consent_timestamp: Optional[int] = None
    consent_captured_by_user_id: Optional[str] = None
    consent_withdrawal_at: Optional[int] = None
    badge_qr_token: Optional[str] = None
    badge_format: Optional[BadgeFormat] = None
    badge_generation_time: Optional[int] = None
    badge_expiry: Optional[int] = None
    badge_pdf_object_key: Optional[str] = None
    denial_reason: Optional[str] = None
    denied_by: Optional[str] = None
    # Fields for resuming draft registration
    purpose: Optional[str] = None
    host_id: Optional[str] = None
    visitor_name_snapshot: Optional[str] = None
    company_snapshot: Optional[str] = None
    host_name_snapshot: Optional[str] = None
    department_name_snapshot: Optional[str] = None
    receptionist_name_snapshot: Optional[str] = None
    check_in_method: Optional[CheckInMethod] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitSessionOut(VisitSessionBase):
    id: Optional[str] = Field(default=None, alias="_id")
    check_in_time: Optional[int] = None
    check_out_time: Optional[int] = None
    date_created: Optional[int] = None
    visit_duration: Optional[int] = None  # computed: check_out_time - check_in_time

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        # Compute visit_duration if both times are available
        check_in = values.get("check_in_time")
        check_out = values.get("check_out_time")
        if check_in and check_out:
            values["visit_duration"] = check_out - check_in
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


from schemas.summary_schema import (  # noqa: E402
    TenantBriefSummary,
    DepartmentBriefSummary,
    UserBriefSummary,
    VisitorBriefSummary,
    VisitorProfileBriefSummary,
    AppointmentBriefSummary,
)


class VisitSessionWithSummaryOut(VisitSessionOut):
    """VisitSessionOut enriched with snapshots of every entity it references."""

    tenant_summary: Optional[TenantBriefSummary] = None
    department_summary: Optional[DepartmentBriefSummary] = None
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None
    host_summary: Optional[UserBriefSummary] = None
    receptionist_summary: Optional[UserBriefSummary] = None
    appointment_summary: Optional[AppointmentBriefSummary] = None
    verified_by_summary: Optional[UserBriefSummary] = None
    consent_captured_by_summary: Optional[UserBriefSummary] = None
    denied_by_summary: Optional[UserBriefSummary] = None


# Request schemas for check-in/check-out endpoints
class CheckInRequest(BaseModel):
    phone: str
    full_name: str
    company: Optional[str] = None
    department_id: str
    host_id: Optional[str] = None
    purpose: Optional[str] = None
    appointment_id: Optional[str] = None
    check_in_method: CheckInMethod = CheckInMethod.MANUAL
    photo_object_key: Optional[str] = None
    id_image_object_key: Optional[str] = None
    consent_granted: Optional[bool] = None


class ConfirmCheckInRequest(BaseModel):
    badge_format: Optional[BadgeFormat] = BadgeFormat.A7
    purpose: Optional[str] = None
    host_id: Optional[str] = None


class AppointmentCheckInRequest(BaseModel):
    """Body for ``POST /v1/appointments/{appointment_id}/check-in``.

    Every field is optional — the service hydrates from the appointment's
    snapshots and the linked visitor profile when omitted. Override
    fields when the receptionist needs to correct or supplement the
    on-record data at the desk."""

    phone: Optional[str] = None
    full_name: Optional[str] = None
    company: Optional[str] = None
    photo_object_key: Optional[str] = None
    id_image_object_key: Optional[str] = None
    consent_granted: Optional[bool] = None
    badge_format: Optional[BadgeFormat] = BadgeFormat.A7
    issue_badge: bool = True


class DenyVisitorRequest(BaseModel):
    reason: str


class CheckOutRequest(BaseModel):
    badge_qr_token: Optional[str] = None
    session_id: Optional[str] = None
    checkin_id: Optional[str] = None
    appointment_id: Optional[str] = None
    checkout_id: Optional[str] = None
    source_type: Optional[str] = None
    check_out_method: CheckOutMethod = CheckOutMethod.QR_SCAN


class AwaitingCheckoutItem(BaseModel):
    """Unified row for the manual checkout selector.

    ``source_type`` identifies which underlying collection owns ``checkout_id``:
    ``visit_session``, ``approved_checkin``, or ``scheduled_appointment``.
    """

    id: str
    source_type: str
    checkout_id: str
    tenant_id: str
    status: str
    visitor_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    portrait_url: Optional[str] = None
    verified: Optional[bool] = None
    purpose: Optional[str] = None
    purpose_details: Optional[str] = None
    expected_duration_minutes: Optional[int] = None
    eligible_since: Optional[int] = None
    check_in_time: Optional[int] = None
    approved_at: Optional[int] = None
    scheduled_datetime: Optional[int] = None
    badge_qr_token: Optional[str] = None
    department_id: Optional[str] = None
    host_id: Optional[str] = None
    visitor_id: Optional[str] = None
    visitor_profile_id: Optional[str] = None
    appointment_id: Optional[str] = None
    tenant_summary: Optional[TenantBriefSummary] = None
    department_summary: Optional[DepartmentBriefSummary] = None
    visitor_summary: Optional["VisitorBriefSummary"] = None
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None
    host_summary: Optional[UserBriefSummary] = None
    receptionist_summary: Optional[UserBriefSummary] = None
    appointment_summary: Optional[AppointmentBriefSummary] = None
    details: dict[str, Any] = Field(default_factory=dict)


class CheckoutResult(BaseModel):
    """Unified response for ``POST /v1/visitors/check-out`` across all
    three sources, with timing math pre-computed for the frontend.

    The frontend never needs to subtract timestamps itself — every
    field the UI renders next to the visitor's name (how long they
    stayed, whether they ran over, the moment they walked out) is
    on this object.
    """

    id: str
    source_type: str  # "visit_session" | "approved_checkin" | "scheduled_appointment"
    checkout_id: str
    status: str  # terminal status: "checked_out" or "fulfilled"

    # Timing — same names regardless of source
    eligible_since: Optional[int] = None
    eligible_since_field: Optional[str] = None  # "check_in_time" / "approved_at" / "scheduled_datetime"
    checked_out_at: int

    actual_duration_seconds: Optional[int] = None
    actual_duration_minutes: Optional[float] = None
    expected_duration_minutes: Optional[int] = None
    duration_variance_seconds: Optional[int] = None  # actual - expected*60; negative = early

    check_out_method: Optional[CheckOutMethod] = None

    # Source record. Exactly one of the next three is populated.
    visit_session: Optional[Any] = None
    checkin: Optional[Any] = None
    appointment: Optional[Any] = None

    class Config:
        arbitrary_types_allowed = True
