from __future__ import annotations

import time
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

from repositories.visit_session_repo import (
    create_visit_session,
    get_visit_session,
    get_visit_session_by_badge_token,
    get_active_visitors,
    update_visit_session,
    get_visit_sessions,
)
from repositories.visitor_profile_repo import get_visitor_profile, update_visitor_profile
from repositories.appointment_repo import get_appointment, update_appointment
from repositories.privacy_notice_repo import get_active_notice_for_tenant
from repositories.tenant_repo import get_tenant
from repositories.system_user_repo import get_system_user
from repositories.department_repo import get_department
from schemas.visit_session_schema import (
    VisitSessionCreate,
    VisitSessionUpdate,
    VisitSessionOut,
    CheckInRequest,
    CheckOutRequest,
)
from schemas.visitor_profile_schema import VisitorProfileUpdate
from schemas.appointment_schema import AppointmentUpdate
from schemas.imports import (
    VisitStatus,
    CheckInMethod,
    CheckOutMethod,
    VerificationStatus,
    VerificationMethod,
    LawfulBasis,
    AppointmentStatus,
)
from services.visitor_profile_service import get_or_create_visitor_profile
from services.qr_service import sign_badge_token, verify_badge_token
from services.badge_service import generate_badge_pdf


async def check_in_visitor(
    request: CheckInRequest,
    tenant_id: str,
    receptionist_id: str,
) -> dict:
    """Full check-in flow: profile lookup/create, consent, session, badge."""
    # 1. Get tenant settings
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # 2. Get or create visitor profile
    profile = await get_or_create_visitor_profile(
        tenant_id=tenant_id,
        phone=request.phone,
        full_name=request.full_name or "Unknown",
        company=request.company,
        photo_url=request.photo_url,
    )

    # 3. Get department and host info for snapshots
    department = await get_department({"_id": ObjectId(request.department_id), "tenant_id": tenant_id})
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    host_name = None
    if request.host_id and ObjectId.is_valid(request.host_id):
        host = await get_system_user({"_id": ObjectId(request.host_id)})
        host_name = host.name if host else None

    receptionist = await get_system_user({"_id": ObjectId(receptionist_id)})
    receptionist_name = receptionist.name if receptionist else None

    # 4. Get active privacy notice
    notice = await get_active_notice_for_tenant(tenant_id)
    notice_id = notice.id if notice else None

    # 5. Determine consent requirements based on tenant lawful basis
    lawful_basis = LawfulBasis(tenant.lawful_basis) if isinstance(tenant.lawful_basis, str) else tenant.lawful_basis
    consent_granted = None
    consent_method = None
    consent_timestamp_val = None

    if lawful_basis == LawfulBasis.CONSENT:
        if not request.consent_granted:
            raise HTTPException(
                status_code=400,
                detail="Consent is required for check-in. Tenant requires explicit consent.",
            )
        consent_granted = True
        consent_method = "tap_to_agree"
        consent_timestamp_val = int(time.time())

    # 6. Determine verification status
    verification_status = VerificationStatus.UNVERIFIED
    verification_method = None
    if request.check_in_method == CheckInMethod.ID_SCAN and request.id_image_url:
        verification_status = VerificationStatus.VERIFIED
        verification_method = VerificationMethod.ID_SCAN
    elif request.check_in_method == CheckInMethod.QR:
        verification_method = VerificationMethod.QR_UPLOAD

    # 7. Create visit session
    session_data = VisitSessionCreate(
        tenant_id=tenant_id,
        visitor_profile_id=profile.id,
        department_id=request.department_id,
        host_id=request.host_id,
        receptionist_id=receptionist_id,
        privacy_notice_version_id=notice_id,
        check_in_method=request.check_in_method,
        verification_status=verification_status,
        verification_method=verification_method,
        status=VisitStatus.CHECKED_IN,
        purpose=request.purpose,
        visitor_name_snapshot=profile.full_name,
        visitor_company_snapshot=profile.company,
        host_name_snapshot=host_name,
        department_name_snapshot=department.name,
        receptionist_name_snapshot=receptionist_name,
        notice_displayed=notice is not None,
        consent_granted=consent_granted,
        consent_method=consent_method,
        consent_timestamp=consent_timestamp_val,
        consent_captured_by=receptionist_id if consent_granted else None,
        lawful_basis_at_time=lawful_basis,
    )
    session = await create_visit_session(session_data)

    # 8. Generate badge with signed QR token
    badge_token = sign_badge_token(session.id, expiry_hours=24)
    now = datetime.now(timezone.utc)
    badge_pdf_bytes = generate_badge_pdf(
        visitor_name=profile.full_name,
        company=profile.company,
        host_department=f"{host_name or 'N/A'} / {department.name}",
        date_str=now.strftime("%Y-%m-%d"),
        time_in_str=now.strftime("%H:%M"),
        qr_data=badge_token,
        badge_format="A7",
    )

    # 9. Update session with badge info
    session = await update_visit_session(
        {"_id": ObjectId(session.id)},
        VisitSessionUpdate(
            badge_qr_token=badge_token,
            badge_format="A7",
            badge_generation_time=int(time.time()),
            badge_expiry=int(time.time()) + 86400,
        ),
    )

    # 10. Update visitor profile last visit date
    await update_visitor_profile(
        {"_id": ObjectId(profile.id)},
        VisitorProfileUpdate(last_verified_at=int(time.time())),
    )

    return {
        "session": session,
        "visitor_profile": profile,
        "badge_pdf_base64": __import__("base64").b64encode(badge_pdf_bytes).decode("utf-8"),
        "badge_qr_token": badge_token,
    }


async def check_out_visitor(request: CheckOutRequest, tenant_id: str) -> VisitSessionOut:
    """Check out a visitor by badge QR token or session ID."""
    session = None

    if request.badge_qr_token:
        # Verify the HMAC-signed token
        session_id = verify_badge_token(request.badge_qr_token)
        if not session_id:
            raise HTTPException(status_code=400, detail="Invalid or expired badge QR token")
        session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    elif request.session_id:
        if not ObjectId.is_valid(request.session_id):
            raise HTTPException(status_code=400, detail="Invalid session ID format")
        session = await get_visit_session({"_id": ObjectId(request.session_id), "tenant_id": tenant_id})

    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    if session.status != VisitStatus.CHECKED_IN.value and session.status != "checked_in":
        raise HTTPException(status_code=400, detail=f"Visitor is not currently checked in (status: {session.status})")

    updated = await update_visit_session(
        {"_id": ObjectId(session.id)},
        VisitSessionUpdate(
            status=VisitStatus.CHECKED_OUT,
            check_out_method=request.check_out_method,
            check_out_time=int(time.time()),
        ),
    )
    return updated


async def retrieve_active_visitors(tenant_id: str, department_id: str = None):
    return await get_active_visitors(tenant_id=tenant_id, department_id=department_id)


async def retrieve_visit_session_by_id(session_id: str, tenant_id: str) -> VisitSessionOut:
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    result = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Visit session not found")
    return result


async def retrieve_visit_sessions(tenant_id: str, department_id: str = None, start=0, stop=100):
    filter_dict = {"tenant_id": tenant_id}
    if department_id:
        filter_dict["department_id"] = department_id
    return await get_visit_sessions(filter_dict=filter_dict, start=start, stop=stop)
