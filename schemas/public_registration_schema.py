from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr

from schemas.checkin_schema import CheckinPurpose


class PublicRegistrationRequest(BaseModel):
    # full_name is optional when `profile_id` is supplied (returning visitor
    # confirmed via /lookup). Otherwise the service layer rejects the request.
    full_name: Optional[str] = None
    phone: str
    company: Optional[str] = None
    email: Optional[EmailStr] = None
    purpose: Optional[str] = None
    department_id: Optional[str] = None
    appointment_id: Optional[str] = None
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None
    # Signed QR token from /public/register/verify. When present its scope
    # overrides client-supplied department_id / branch_id.
    registration_token: Optional[str] = None
    # Returning-visitor shortcut: opaque id returned from /lookup's confirm step.
    profile_id: Optional[str] = None


class PublicRegistrationResponse(BaseModel):
    session_id: str
    visitor_profile_id: str
    status: str
    message: str


class PublicDepartmentOut(BaseModel):
    id: str
    name: str


class PublicTenantInfoOut(BaseModel):
    tenant_id: str
    company_name: str


class PublicPrivacyNoticeOut(BaseModel):
    notice_id: Optional[str] = None
    title: str
    content: str
    version: Optional[str] = None


class PublicCheckoutResponse(BaseModel):
    session_id: str
    status: str
    visit_duration: Optional[int] = None


class PublicAppointmentLookupOut(BaseModel):
    appointment_id: str
    host_id: Optional[str] = None
    host_name: Optional[str] = None
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    scheduled_at: Optional[int] = None
    purpose: Optional[str] = None


class PublicRegistrationTokenVerifyOut(BaseModel):
    """Response from GET /public/register/verify. scope fields mirror the signed
    registration token; the client uses them to prefill + lock form inputs."""

    valid: bool
    tenant_id: Optional[str] = None
    department_id: Optional[str] = None
    branch_id: Optional[str] = None
    company_name: Optional[str] = None


class PublicIdScanOut(BaseModel):
    """OCR extraction result. No session is created and no image is persisted."""

    full_name: Optional[str] = None
    id_number: Optional[str] = None
    id_type: Optional[str] = None
    confidence: float = 0.0


class PublicReturningVisitorLookupRequest(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None


class PublicReturningVisitorLookupOut(BaseModel):
    """Masked projection of a matched returning visitor. Raw PII is never
    returned — the caller must re-supply `phone` on register to confirm."""

    found: bool
    profile_id: Optional[str] = None
    full_name_masked: Optional[str] = None
    company: Optional[str] = None
    last_visit_ago_days: Optional[int] = None
    id_verified_recently: bool = False


class PublicFinalizeRequest(BaseModel):
    session_id: str
    receptionist_code: str


class PublicVisitorStatusRequest(BaseModel):
    """Lookup body for the public visitor-status endpoint. At least one of
    ``email`` or ``phone`` must be supplied."""

    phone: Optional[str] = None
    email: Optional[EmailStr] = None


class PublicVisitorStatusOut(BaseModel):
    """Non-PII recognition payload for a returning visitor.

    Intentionally minimal — no name, email, phone, company, id_type, or
    profile_id is returned. The endpoint confirms whether the tenant has seen
    this visitor before and exposes the counters needed to drive the kiosk
    UX ("welcome back", skip ID re-scan).

    ``visitor_id`` is the only identifier returned. It is the id of the
    ``visitors`` collection record (not the profile id) and is required to
    drive the id-based returning-visitor submit endpoint. It is null when a
    profile matched but no ``visitors`` record exists yet — in that case the
    frontend must fall back to the email/phone-based submit.

    All PII stays server-side; edits are reserved for authenticated
    receptionist / super_admin endpoints."""

    found: bool
    visitor_id: Optional[str] = None
    total_visits: Optional[int] = None
    last_visit_ago_days: Optional[int] = None
    id_verified_recently: bool = False


class PublicReturningVisitorSubmitRequest(BaseModel):
    """Minimal submit body for a recognised returning visitor.

    Use when ``visitor_id`` is already known (returned by
    ``/visitor-status``). Email, phone, and bio_data are intentionally
    omitted — they are loaded from the stored visitor record server-side.

    ``tenant_specific_data`` only has to be supplied when the tenant's
    active check-in config declares required fields in the
    ``tenant_specific`` category. If the config has no required
    tenant-specific fields the frontend can submit this with an empty
    object (or omit it)."""

    visitor_id: str
    purpose: CheckinPurpose
    tenant_specific_data: dict = {}
