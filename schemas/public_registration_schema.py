from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr

from schemas.checkin_schema import CheckinPurpose
from schemas.imports import NoticeDisplayMode


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
    # Consent capture (see backend-docs visitor privacy notice contract A.5).
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    privacy_notice_id: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None
    consent_accepted_at: Optional[int] = None
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
    """What the kiosk fetches before data capture (contract A.4).

    ``id`` / ``version_id`` are the values the kiosk echoes back on submit so
    the consent record pins to the exact notice text the visitor accepted.
    Legacy ``notice_id`` / ``content`` / ``version`` aliases are retained so
    older frontend builds keep working.
    """

    id: Optional[str] = None
    title: str
    summary: Optional[str] = None
    full_text: Optional[str] = None
    display_mode: NoticeDisplayMode = NoticeDisplayMode.ACTIVE_CONSENT
    version_id: Optional[str] = None
    effective_date: Optional[int] = None
    # Legacy fields (kept for backward compatibility with older kiosk builds).
    notice_id: Optional[str] = None
    content: str = ""
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


class PublicBadgePassTenant(BaseModel):
    """Tenant sub-object on the public printable badge pass."""

    company_name: str
    logo_url: Optional[str] = None
    branding_enabled: bool = False


class PublicBadgeBranding(BaseModel):
    """Custom-branding block for the unified badge renderer (WS7).

    Present only when the tenant's plan grants the ``custom_branding``
    feature flag — non-branded orgs get ``branding: null`` and the frontend
    renders the neutral VisiChek layout."""

    header_color: Optional[str] = None
    text_color: Optional[str] = None
    logo_url: Optional[str] = None
    logo_position: Optional[str] = None
    company_display_name: Optional[str] = None


class PublicBadgePassOut(BaseModel):
    """Public printable visitor badge — reachable by anyone holding the
    visitor's ``badge_qr_token``. Carries only the non-sensitive fields the
    badge prints (no email, phone, ID number, or portrait). The host is
    exposed by display name only — contact details (email/phone) are never
    put on the public pass."""

    token: str
    visitor_name: str
    company: Optional[str] = None
    purpose: Optional[str] = None
    host_name: Optional[str] = None
    department_name: Optional[str] = None
    status: str
    issued_at: Optional[int] = None
    # ``expires_at`` is None when the org's badge-expiry policy is MANUAL —
    # the badge stays valid until the visit is checked out / revoked.
    expires_at: Optional[int] = None
    # When the visitor actually entered (visit-session ``check_in_time`` or
    # the check-in's ``approved_at``).
    check_in_time: Optional[int] = None
    tenant: PublicBadgePassTenant
    branding: Optional[PublicBadgeBranding] = None


class PublicReturningVisitorSubmitRequest(BaseModel):
    """Minimal submit body for a recognised returning visitor.

    Use when ``visitor_id`` is already known (returned by
    ``/visitor-status``). Email, phone, and bio_data are intentionally
    omitted — they are loaded from the stored visitor record server-side.

    ``tenant_specific_data`` only has to be supplied when the tenant's
    active check-in config declares required fields in the
    ``tenant_specific`` category. If the config has no required
    tenant-specific fields the frontend can submit this with an empty
    object (or omit it).

    ``visitor_lat`` / ``visitor_lng`` are required when the tenant has
    geofencing enabled — see ``backend-docs/geofencing.md``."""

    visitor_id: str
    purpose: CheckinPurpose
    tenant_specific_data: dict = {}
    visitor_lat: Optional[float] = None
    visitor_lng: Optional[float] = None
    visitor_location_accuracy_m: Optional[float] = None
    # Consent capture (contract A.5).
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    privacy_notice_id: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None
    consent_accepted_at: Optional[int] = None
