from __future__ import annotations

import time
from schemas.imports import *
from schemas.summary_schema import ManualVerificationInfo


# Sensible defaults per purpose-of-visit value. Aligned with the
# default ``purpose_of_visit`` enum seeded by
# ``services.tenant_enum_service`` — a tenant can still customise its
# enum values without breaking this map (custom values fall through to
# ``DEFAULT_VISIT_DURATION_MINUTES``).
PURPOSE_DURATION_DEFAULTS_MINUTES: dict[str, int] = {
    "meeting": 60,
    "interview": 60,
    "delivery": 15,
    "contractor": 120,
    "event": 120,
    "tour": 30,
    "personal": 30,
    "other": 30,
}
DEFAULT_VISIT_DURATION_MINUTES = 30


class CheckinPurpose(BaseModel):
    purpose: str
    purpose_details: Optional[str] = None
    expected_duration_minutes: Optional[int] = None

    @model_validator(mode="after")
    def _fill_default_duration(self) -> "CheckinPurpose":
        # Only apply the default when the kiosk doesn't supply a value;
        # an explicit ``0`` from the kiosk still wins (treated as
        # "indefinite / unspecified").
        if self.expected_duration_minutes is None:
            key = (self.purpose or "").strip().lower()
            self.expected_duration_minutes = PURPOSE_DURATION_DEFAULTS_MINUTES.get(
                key, DEFAULT_VISIT_DURATION_MINUTES
            )
        return self


class CheckinBase(BaseModel):
    tenant_id: str
    visitor_id: str
    checkin_config_id: str
    # Branch this check-in belongs to. Resolved from the registration QR
    # token's scope (kiosk/public flow) or the staff member's token, falling
    # back to the tenant HQ. Branch-null check-ins predate branch separation
    # and are treated as HQ data. Promoted from the legacy
    # ``tenant_specific_data['branch_id']`` to a first-class field so reads
    # can filter on it and branch_filter() applies.
    branch_id: Optional[str] = None
    id_extraction_id: Optional[str] = None
    tenant_specific_data: dict
    purpose: CheckinPurpose
    state: CheckinState = CheckinState.PENDING_APPROVAL
    verified: bool = False
    # How the visitor's identity was verified for this check-in. None until
    # a verification path sets it; "manual" when staff vouched by hand.
    verification_method: Optional[VerificationMethod] = None
    # Attribution snapshot for a staff-vouched (manual) verification; null
    # for automated (id_scan / qr_upload / host_approval) or unverified.
    manual_verification: Optional[ManualVerificationInfo] = None
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[int] = None
    # Internal note supplied by the approving staff member; surfaced in the
    # check-in detail view and echoed into the approval audit event.
    approval_notes: Optional[str] = None
    rejection_reason: Optional[str] = None
    checked_out_at: Optional[int] = None


class CheckinCreate(CheckinBase):
    # tenant_id is token-derived and injected by the route; never client-supplied.
    tenant_id: str = ""
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinUpdate(BaseModel):
    state: Optional[CheckinState] = None
    verified: Optional[bool] = None
    verification_method: Optional[VerificationMethod] = None
    manual_verification: Optional[ManualVerificationInfo] = None
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[int] = None
    approval_notes: Optional[str] = None
    rejection_reason: Optional[str] = None
    tenant_specific_data: Optional[dict] = None
    checked_out_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinOut(CheckinBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    # Short-lived signed capability token, populated ONLY on the check-in
    # creation response (kiosk submit / receptionist create). The public
    # KYC follow-up endpoints (initiate / skip / status) require it. Never
    # persisted to the DB and never present on list/read responses — a bare
    # check-in id must not authorize KYC actions.
    capability_token: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class CheckinSubmitRequest(BaseModel):
    visitor_id: Optional[str] = None
    id_extraction_id: Optional[str] = None
    bio_data: dict
    tenant_specific_data: dict
    purpose: CheckinPurpose


class CheckinConfirmRequest(BaseModel):
    action: str  # "approve" or "reject"
    notes: Optional[str] = None


class CheckinManualVerifyRequest(BaseModel):
    """Body for POST /v1/checkins/{checkin_id}/manual-verify.

    The verifier identity is taken from the authenticated session — never
    from the body. ``notes`` is the only accepted field (the staff member's
    note, e.g. "Checked national ID card against the visitor in person")."""

    notes: Optional[str] = None


class CheckinListItem(BaseModel):
    visitor_name: str
    verified: bool
    purpose: str
    host_employee_id: Optional[str] = None
    created_at: int


from schemas.summary_schema import (  # noqa: E402
    VisitorBriefSummary,
    BranchBriefSummary,
)


class IdentityCheckSummary(BaseModel):
    """Why this visitor is (or isn't) verified — always populated.

    The receptionist decides who walks through the door, so "Not verified" on
    its own is not enough: it reads identically whether the visitor skipped the
    ID step, the check never ran, or the ID they presented **belongs to someone
    else**. Those demand very different responses, so every check-in carries an
    explicit reason rather than leaving the receptionist to infer one.

    ``reason`` is required — there is no code path that surfaces a verification
    state without saying why.
    """

    verified: bool
    # KYC status, or "not_started" / "manual" when no provider check applies.
    status: str
    # Short label for the badge, e.g. "ID mismatch".
    headline: str
    # The full explanation shown to the receptionist. Always present.
    reason: str
    # True only for the dangerous case: a genuine ID that belongs to a
    # different person than the one who filled in the form.
    mismatch: bool = False
    # What the ID actually said, when it disagreed with what was typed.
    extracted_name: Optional[str] = None
    name_score: Optional[float] = None


class PendingApprovalItem(BaseModel):
    """Unified row for the pending-approvals UI.

    The receptionist's approval queue mixes two sources:

    * ``checkin``     — a kiosk submission awaiting receptionist
                        approval (status mirrors ``CheckinState``,
                        usually ``pending_approval``).
    * ``appointment`` — a SCHEDULED appointment the host pre-vetted;
                        ``state`` is the literal string ``"scheduled"``
                        and ``verified`` is true because the host
                        attached the visitor's photo / identity ahead
                        of time.

    The ``id`` is whichever underlying record's id; ``source_type``
    tells the frontend which collection to use when the receptionist
    actions the row (approve a checkin → ``POST /checkins/{id}/confirm``;
    a scheduled appointment becomes a real visit via
    ``POST /v1/appointments/{id}/check-in``)."""

    id: str
    source_type: str  # "checkin" | "appointment"
    tenant_id: str
    state: str
    verified: bool = False
    visitor_name: Optional[str] = None
    company: Optional[str] = None
    purpose: Optional[str] = None
    expected_duration_minutes: Optional[int] = None
    photo_url: Optional[str] = None  # presigned URL when available
    department_id: Optional[str] = None
    host_id: Optional[str] = None
    scheduled_datetime: Optional[int] = None
    created_at: int
    visitor: Optional["VisitorBriefSummary"] = None
    # When source_type=="appointment", carries the appointment's id again
    # so the frontend can call /v1/appointments/{id}/check-in directly.
    appointment_id: Optional[str] = None
    # When source_type=="checkin", carries the checkin record id (same as
    # ``id``); kept for symmetry with appointment_id.
    checkin_id: Optional[str] = None
    # Why this row is (or isn't) verified. Never None for checkin rows.
    identity_check: Optional["IdentityCheckSummary"] = None


class CheckinWithVisitorOut(CheckinOut):
    """``CheckinOut`` with an embedded visitor snapshot so the approver UI
    can render the visitor's name, contact info, and verification state
    without issuing a second request per row.

    ``visitor`` is ``None`` only if the referenced visitor was deleted or
    the id is invalid — in which case the row is surfaced anyway so the
    approver can still see the pending state and reject it."""

    visitor: Optional[VisitorBriefSummary] = None
    branch_summary: Optional[BranchBriefSummary] = None
    # Why this check-in is (or isn't) verified — see ``IdentityCheckSummary``.
    identity_check: Optional[IdentityCheckSummary] = None
