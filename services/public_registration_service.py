from __future__ import annotations

import time
from typing import Optional

from bson import ObjectId
from fastapi import HTTPException

from repositories.visit_session_repo import (
    count_visit_sessions,
    create_visit_session,
    get_visit_session,
    update_visit_session,
)
from repositories.tenant_repo import get_tenant
from repositories.department_repo import get_department, get_departments
from repositories.appointment_repo import get_appointment
from repositories.privacy_notice_repo import get_active_notice_for_tenant
from repositories.visitor_profile_repo import (
    get_visitor_profile,
    get_visitor_profile_by_email,
    get_visitor_profile_by_phone,
    increment_visitor_profile_visits,
)
from repositories.system_user_repo import get_system_user
from repositories.tenant_settings_repo import get_tenant_settings
from schemas.public_registration_schema import (
    PublicAppointmentLookupOut,
    PublicCheckoutResponse,
    PublicDepartmentOut,
    PublicFinalizeRequest,
    PublicIdScanOut,
    PublicPrivacyNoticeOut,
    PublicRegistrationRequest,
    PublicRegistrationResponse,
    PublicRegistrationTokenVerifyOut,
    PublicReturningVisitorLookupOut,
    PublicReturningVisitorLookupRequest,
    PublicTenantInfoOut,
    PublicVisitorStatusOut,
    PublicVisitorStatusRequest,
)
from schemas.imports import (
    VisitStatus,
    CheckInMethod,
    CheckOutMethod,
    AppointmentStatus,
)
from schemas.visit_session_schema import VisitSessionCreate, VisitSessionUpdate
from services.visitor_profile_service import get_or_create_visitor_profile
from services.qr_service import verify_badge_token, verify_registration_token
from services.plan_limits import enforce_entity_cap, get_month_bounds


async def register_visitor_public(
    tenant_id: str,
    request: PublicRegistrationRequest,
) -> PublicRegistrationResponse:
    """Public visitor self-registration (no auth). Creates profile + REGISTERED session."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # If a signed registration token is supplied, its scope overrides any
    # client-supplied department_id. Tokens are bound to a single tenant.
    token_scope = None
    registration_token_id: Optional[str] = None
    if request.registration_token:
        token_scope = verify_registration_token(request.registration_token)
        if not token_scope or token_scope.get("tenant_id") != tenant_id:
            raise HTTPException(
                status_code=400, detail="Invalid or expired registration token"
            )
        if token_scope.get("department_id"):
            request.department_id = token_scope["department_id"]
        # Phase A3 (Issue 5 audit follow-up). Stable, non-reversible
        # identifier so we can record which QR shaped this registration
        # on the audit trail without persisting the replayable token
        # itself.
        import hashlib

        registration_token_id = hashlib.sha256(
            request.registration_token.encode("utf-8")
        ).hexdigest()[:16]

    # Returning-visitor shortcut: if profile_id is supplied it must match the
    # phone. full_name can then be omitted and is pulled from the profile.
    existing_profile = None
    if request.profile_id:
        if not ObjectId.is_valid(request.profile_id):
            raise HTTPException(status_code=400, detail="Invalid profile_id")
        existing_profile = await get_visitor_profile(
            {"_id": ObjectId(request.profile_id), "tenant_id": tenant_id},
        )
        if not existing_profile or existing_profile.phone != request.phone:
            raise HTTPException(
                status_code=400, detail="Returning-visitor match failed"
            )
        if not request.full_name:
            request.full_name = existing_profile.full_name
        if not request.company:
            request.company = existing_profile.company

    if not request.full_name:
        raise HTTPException(status_code=400, detail="full_name is required")

    # Enforce plan cap on visit sessions created this calendar month
    month_start, month_end = get_month_bounds()
    month_count = await count_visit_sessions(
        {
            "tenant_id": tenant_id,
            "check_in_time": {"$gte": month_start, "$lt": month_end},
        }
    )
    await enforce_entity_cap(
        tenant_id=tenant_id,
        cap_key="max_visitors_per_month",
        current_count=month_count,
        friendly_name="Monthly visitor",
    )

    # Consent enforcement based on tenant's lawful basis
    from schemas.imports import LawfulBasis

    lawful_basis = None
    consent_granted = None
    consent_method = None
    consent_timestamp_val = None

    if hasattr(tenant, "lawful_basis") and tenant.lawful_basis:
        try:
            lawful_basis = LawfulBasis(tenant.lawful_basis)
        except (ValueError, KeyError):
            lawful_basis = None

    if lawful_basis == LawfulBasis.CONSENT:
        if not getattr(request, "consent_granted", None):
            raise HTTPException(
                status_code=400,
                detail="Consent is required for registration. Please accept the privacy notice.",
            )
        consent_granted = True
        consent_method = (
            getattr(request, "consent_method", None) or "digital_acceptance"
        )
        consent_timestamp_val = int(time.time())

    # Get or create visitor profile
    profile = await get_or_create_visitor_profile(
        tenant_id=tenant_id,
        phone=request.phone,
        full_name=request.full_name,
        company=request.company,
    )

    # Increment visit count
    await increment_visitor_profile_visits({"_id": ObjectId(profile.id)})

    # Resolve department
    department_name = None
    department_id = request.department_id
    if department_id and ObjectId.is_valid(department_id):
        dept = await get_department(
            {"_id": ObjectId(department_id), "tenant_id": tenant_id}
        )
        if dept:
            department_name = dept.name

    # Handle appointment linkage. The appointment stays SCHEDULED until
    # the badge is actually issued (confirm_check_in) — public
    # registration only creates a REGISTERED session.
    host_id = None
    appointment_id = request.appointment_id
    if appointment_id and ObjectId.is_valid(appointment_id):
        appointment = await get_appointment(
            {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
        )
        if appointment and appointment.status == AppointmentStatus.SCHEDULED.value:
            host_id = appointment.host_id if hasattr(appointment, "host_id") else None
            if not department_id and hasattr(appointment, "department_id"):
                department_id = appointment.department_id

    # Create visit session with REGISTERED status
    session_data = VisitSessionCreate(
        tenant_id=tenant_id,
        visitor_profile_id=profile.id or "",
        department_id=department_id or "",
        host_id=host_id,
        appointment_id=appointment_id,
        check_in_method=CheckInMethod.QR,
        status=VisitStatus.REGISTERED,
        purpose=request.purpose,
        visitor_name_snapshot=profile.full_name,
        company_snapshot=profile.company,
        department_name_snapshot=department_name,
        consent_notice_displayed=True
        if getattr(request, "privacy_notice_version_id", None)
        else False,
        consent_granted=consent_granted,
        consent_method=consent_method,
        consent_timestamp=consent_timestamp_val,
        privacy_notice_version_id=getattr(request, "privacy_notice_version_id", None),
        lawful_basis_at_time=lawful_basis,
    )
    session = await create_visit_session(session_data)

    # Phase A3 audit hook (Issue 5). When a registration QR shaped the
    # visit, record a focused event tying the resulting visit session
    # to the token id and resolved scope. We hold the raw token in a
    # one-way hash so the audit log doesn't contain replayable
    # secrets. Fire-and-forget — an audit failure must not roll back
    # an otherwise-successful registration.
    if token_scope and registration_token_id:
        try:
            from services.audit_service import record_audit_event

            await record_audit_event(
                actor_id=profile.id or "",
                actor_role="public_visitor",
                action="visit_session.registered_via_qr",
                resource_type="visit_session",
                resource_id=session.id or "",
                tenant_id=tenant_id,
                details={
                    "registration_token_id": registration_token_id,
                    "registration_token_scope": {
                        "department_id": token_scope.get("department_id"),
                        "branch_id": token_scope.get("branch_id"),
                    },
                },
            )
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "visit_session.registered_via_qr audit record failed for session %s",
                session.id,
                exc_info=True,
            )

    return PublicRegistrationResponse(
        session_id=session.id or "",
        visitor_profile_id=profile.id or "",
        status=session.status,
        message="Registration successful. Please proceed to reception.",
    )


async def checkout_visitor_public(badge_qr_token: str) -> PublicCheckoutResponse:
    """Public self-checkout via badge QR token (no auth)."""
    session_id = verify_badge_token(badge_qr_token)
    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid or expired badge QR token")

    session = await get_visit_session({"_id": ObjectId(session_id)})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    if session.status not in (VisitStatus.CHECKED_IN.value, "checked_in"):
        raise HTTPException(
            status_code=400,
            detail=f"Visitor is not currently checked in (status: {session.status})",
        )

    checkout_time = int(time.time())
    updated = await update_visit_session(
        {"_id": ObjectId(session_id)},
        VisitSessionUpdate(
            status=VisitStatus.CHECKED_OUT,
            check_out_method=CheckOutMethod.QR_SCAN,
            check_out_time=checkout_time,
        ),
    )

    visit_duration = None
    if updated.check_in_time:
        visit_duration = checkout_time - updated.check_in_time

    return PublicCheckoutResponse(
        session_id=updated.id or "",
        status=updated.status,
        visit_duration=visit_duration,
    )


async def list_public_departments(tenant_id: str) -> list[PublicDepartmentOut]:
    departments = await get_departments({"tenant_id": tenant_id, "is_active": True})
    return [PublicDepartmentOut(id=d.id or "", name=d.name) for d in departments]


async def get_public_tenant_info(tenant_id: str) -> PublicTenantInfoOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return PublicTenantInfoOut(
        tenant_id=tenant.id or "",
        company_name=tenant.company_name,
    )


async def get_public_privacy_notice(tenant_id: str) -> PublicPrivacyNoticeOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    notice = await get_active_notice_for_tenant(tenant_id)
    if not notice:
        return PublicPrivacyNoticeOut(
            notice_id=None,
            title="No privacy notice available",
            content="",
            version=None,
        )

    return PublicPrivacyNoticeOut(
        notice_id=notice.id,
        title=getattr(notice, "title", "Privacy Notice"),
        content=getattr(notice, "content", ""),
        version=getattr(notice, "version", None),
    )


async def lookup_public_appointment(
    tenant_id: str,
    appointment_id: str,
) -> PublicAppointmentLookupOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID")

    appointment = await get_appointment(
        {
            "_id": ObjectId(appointment_id),
            "tenant_id": tenant_id,
            "status": "scheduled",
        },
    )
    if not appointment:
        raise HTTPException(
            status_code=404, detail="Appointment not found or not in scheduled status"
        )

    return PublicAppointmentLookupOut(
        appointment_id=appointment.id or "",
        host_id=getattr(appointment, "host_id", None),
        host_name=getattr(appointment, "host_name", None),
        department_id=getattr(appointment, "department_id", None),
        department_name=getattr(appointment, "department_name", None),
        scheduled_at=getattr(appointment, "scheduled_at", None),
        purpose=getattr(appointment, "purpose", None),
    )


def _mask_name(full_name: str) -> str:
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return ""

    def _m(s: str) -> str:
        return s[0] + ("*" * max(len(s) - 1, 2)) if s else ""

    return " ".join(_m(p) for p in parts)


async def verify_public_registration_token(
    token: str,
) -> PublicRegistrationTokenVerifyOut:
    """Public pre-flight check for a QR registration token. Used by the form
    to reject expired/tampered tokens before collecting visitor PII."""
    scope = verify_registration_token(token)
    if not scope:
        return PublicRegistrationTokenVerifyOut(valid=False)

    tenant_id = scope.get("tenant_id")
    company_name = None
    if tenant_id and ObjectId.is_valid(tenant_id):
        tenant = await get_tenant({"_id": ObjectId(tenant_id)})
        if tenant:
            company_name = tenant.company_name
        else:
            return PublicRegistrationTokenVerifyOut(valid=False)

    return PublicRegistrationTokenVerifyOut(
        valid=True,
        tenant_id=tenant_id,
        department_id=scope.get("department_id"),
        branch_id=scope.get("branch_id"),
        company_name=company_name,
    )


async def public_ocr_id_scan(
    tenant_id: str, image_bytes: bytes, mime_type: str
) -> PublicIdScanOut:
    """Run OCR on an uploaded ID image and return extracted fields. The image
    is processed in-memory and never persisted — the visitor submits the
    confirmed fields through /public/register/{tenant_id} normally."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image payload")
    # 8 MiB hard cap — the OCR provider will reject larger anyway.
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds 8 MiB limit")

    try:
        from core.ocr.manager import OCRManager

        ocr = OCRManager.get_instance()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="OCR service is not configured")

    try:
        result = await ocr.extract_id(image_bytes, mime_type or "image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OCR extraction failed: {str(e)}")

    return PublicIdScanOut(
        full_name=getattr(result, "full_name", None),
        id_number=getattr(result, "id_number", None),
        id_type=getattr(result, "id_type", None),
        confidence=float(getattr(result, "confidence", 0.0) or 0.0),
    )


async def lookup_returning_visitor(
    tenant_id: str,
    request: PublicReturningVisitorLookupRequest,
) -> PublicReturningVisitorLookupOut:
    """Masked lookup for returning visitors. Returns a masked name and the
    opaque profile_id on match. The caller must re-supply `phone` on submit
    to prove possession — we never return the raw phone/email back."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")
    if not request.phone and not request.email:
        raise HTTPException(status_code=400, detail="phone or email is required")

    profile = None
    if request.phone:
        profile = await get_visitor_profile_by_phone(
            tenant_id=tenant_id, phone=request.phone
        )
    if not profile and request.email:
        profile = await get_visitor_profile_by_email(
            tenant_id=tenant_id, email=request.email
        )

    if not profile:
        return PublicReturningVisitorLookupOut(found=False)

    # Re-verification window check — tenant setting gates the "skip ID scan"
    # fast path. 0 disables the shortcut entirely.
    id_verified_recently = False
    settings = await get_tenant_settings({"tenant_id": tenant_id})
    window_days = getattr(settings, "id_reverification_days", 30) if settings else 30
    if window_days and window_days > 0 and profile.last_verification_date:
        cutoff = int(time.time()) - (window_days * 86400)
        id_verified_recently = profile.last_verification_date >= cutoff

    last_visit_ago_days = None
    if profile.last_visit_date:
        last_visit_ago_days = max(
            (int(time.time()) - profile.last_visit_date) // 86400, 0
        )

    return PublicReturningVisitorLookupOut(
        found=True,
        profile_id=profile.id,
        full_name_masked=_mask_name(profile.full_name),
        company=profile.company,
        last_visit_ago_days=last_visit_ago_days,
        id_verified_recently=id_verified_recently,
    )


async def check_returning_visitor_status(
    tenant_id: str,
    request: PublicVisitorStatusRequest,
) -> PublicVisitorStatusOut:
    """Public recognition endpoint. Looks up a VisitorProfile by phone OR
    email within the tenant and returns a non-PII status payload the kiosk
    uses to drive the "welcome back, skip the details" UX.

    By design this endpoint returns **no** PII — not even a masked name or a
    profile id. The raw fields stay server-side; the submit endpoint
    re-resolves the profile via the email/phone the visitor supplies and
    fills in any required fields that weren't re-typed. Edits to the stored
    PII are reserved for authenticated receptionist endpoints.

    Phone match wins over email when both are supplied (matches the upsert
    order used by ``_upsert_visitor_profile_from_submit``)."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")
    if not request.phone and not request.email:
        raise HTTPException(status_code=400, detail="phone or email is required")

    profile = None
    if request.phone:
        profile = await get_visitor_profile_by_phone(
            tenant_id=tenant_id, phone=request.phone
        )
    if not profile and request.email:
        profile = await get_visitor_profile_by_email(
            tenant_id=tenant_id, email=request.email
        )

    if not profile:
        return PublicVisitorStatusOut(found=False)

    last_visit_ago_days = None
    if profile.last_visit_date:
        last_visit_ago_days = max(
            (int(time.time()) - profile.last_visit_date) // 86400, 0
        )

    id_verified_recently = False
    settings = await get_tenant_settings({"tenant_id": tenant_id})
    window_days = getattr(settings, "id_reverification_days", 30) if settings else 30
    if window_days and window_days > 0 and profile.last_verification_date:
        cutoff = int(time.time()) - (window_days * 86400)
        id_verified_recently = profile.last_verification_date >= cutoff

    # Resolve the matching ``visitors`` record so the frontend can drive the
    # visitor_id-based submit endpoint. A profile can exist without a visitor
    # record for tenants that went through the older self-registration flow
    # — in that case we return null and the frontend falls back to the
    # email/phone-based submit.
    from repositories.visitor_repo import find_visitor_by_email_or_phone_any

    visitor = await find_visitor_by_email_or_phone_any(
        tenant_id=tenant_id, email=request.email, phone=request.phone
    )

    return PublicVisitorStatusOut(
        found=True,
        visitor_id=(visitor.id if visitor else None),
        total_visits=profile.total_visits or 0,
        last_visit_ago_days=last_visit_ago_days,
        id_verified_recently=id_verified_recently,
    )


async def finalize_public_registration(
    tenant_id: str,
    request: PublicFinalizeRequest,
) -> dict:
    """Public endpoint that finalizes a REGISTERED session by naming the
    receptionist who is accepting the visitor. The receptionist_code is the
    receptionist's system_user id (displayed at reception). Delegates to the
    standard confirm_check_in flow so badge generation + audit stay identical."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")
    if not ObjectId.is_valid(request.session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    if not ObjectId.is_valid(request.receptionist_code):
        raise HTTPException(status_code=400, detail="Invalid receptionist code")

    receptionist = await get_system_user(
        {
            "_id": ObjectId(request.receptionist_code),
            "tenant_id": tenant_id,
        }
    )
    if not receptionist or receptionist.role not in ("receptionist", "super_admin"):
        raise HTTPException(status_code=404, detail="Receptionist not found")

    from services.visit_session_service import confirm_check_in

    return await confirm_check_in(
        session_id=request.session_id,
        receptionist_id=receptionist.id or request.receptionist_code,
        tenant_id=tenant_id,
        badge_format="A7",
    )
