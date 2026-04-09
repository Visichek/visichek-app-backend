from __future__ import annotations

import time
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

from repositories.visit_session_repo import (
    count_visit_sessions,
    create_visit_session,
    get_visit_session,
    get_visit_session_by_badge_token,
    get_active_visitors,
    update_visit_session,
    get_visit_sessions,
)
from repositories.visitor_profile_repo import get_visitor_profile, update_visitor_profile, increment_visitor_profile_visits
from repositories.appointment_repo import get_appointment, update_appointment
from repositories.privacy_notice_repo import get_active_notice_for_tenant
from repositories.tenant_repo import get_tenant
from repositories.system_user_repo import get_system_user
from repositories.department_repo import get_department
from schemas.visit_session_schema import (
    VisitSessionCreate,
    VisitSessionUpdate,
    VisitSessionOut,
    VisitSessionWithSummaryOut,
    CheckInRequest,
    CheckOutRequest,
    ConfirmCheckInRequest,
    DenyVisitorRequest,
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
    ProfilingPreference,
)
from services.visitor_profile_service import get_or_create_visitor_profile
from services.qr_service import sign_badge_token, verify_badge_token
from services.badge_service import generate_badge_pdf
from services.plan_limits import enforce_entity_cap, get_month_bounds


async def check_in_visitor(
    request: CheckInRequest,
    tenant_id: str,
    receptionist_id: str,
) -> dict:
    """Phase 1A: Register visitor — create session with status=REGISTERED, no badge yet."""
    # 0. Enforce plan cap on visit sessions created this calendar month
    month_start, month_end = get_month_bounds()
    month_count = await count_visit_sessions({
        "tenant_id": tenant_id,
        "check_in_time": {"$gte": month_start, "$lt": month_end},
    })
    await enforce_entity_cap(
        tenant_id=tenant_id,
        cap_key="max_visitors_per_month",
        current_count=month_count,
        friendly_name="Monthly visitor",
    )

    # 1. Get tenant settings
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # 2. Get or create visitor profile
    profile = await get_or_create_visitor_profile(
        tenant_id=tenant_id,
        phone=request.phone,
        full_name=request.full_name,
        company=request.company,
        photo_object_key=request.photo_object_key,
    )

    # 2A. Check tenant-level profiling config and respect profile opt-out preference
    skip_profile_history = False
    if hasattr(tenant, 'enable_repeat_visitor_recognition') and tenant.enable_repeat_visitor_recognition is False:
        skip_profile_history = True
    if profile.profiling_preference == ProfilingPreference.OPTED_OUT:
        skip_profile_history = True

    # 2B. Handle appointment linkage if provided
    appointment_id = request.appointment_id
    appointment_host_id = None
    appointment_department_id = None

    if request.appointment_id:
        if not ObjectId.is_valid(request.appointment_id):
            raise HTTPException(status_code=400, detail="Invalid appointment ID format")

        appointment = await get_appointment({"_id": ObjectId(request.appointment_id), "tenant_id": tenant_id})
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        # Check appointment status is scheduled
        appt_status = appointment.status if isinstance(appointment.status, str) else appointment.status.value
        if appt_status != AppointmentStatus.SCHEDULED.value and appt_status != "scheduled":
            raise HTTPException(
                status_code=400,
                detail=f"Appointment status must be SCHEDULED, got {appt_status}"
            )

        # Use appointment's host_id and department_id if not provided in request
        if not request.host_id and appointment.host_id:
            appointment_host_id = appointment.host_id
        if not request.department_id and appointment.department_id:
            appointment_department_id = appointment.department_id

    # 3. Get department and host info for snapshots
    # Use appointment's department if request didn't provide one
    dept_id = request.department_id or appointment_department_id
    if not dept_id:
        raise HTTPException(status_code=400, detail="Missing required field: department_id")

    department = await get_department({"_id": ObjectId(dept_id), "tenant_id": tenant_id})
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Use appointment's host if request didn't provide one
    host_id = request.host_id or appointment_host_id
    host_name = None
    if host_id and ObjectId.is_valid(host_id):
        host = await get_system_user({"_id": ObjectId(host_id), "tenant_id": tenant_id})
        if not host:
            raise HTTPException(status_code=404, detail="Host not found in this tenant")
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

    # 6. Determine verification status (based on current check-in method)
    verification_status = VerificationStatus.UNVERIFIED
    verification_method = None
    if request.check_in_method == CheckInMethod.ID_SCAN and request.id_image_object_key:
        verification_status = VerificationStatus.VERIFIED
        verification_method = VerificationMethod.ID_SCAN
    elif request.check_in_method == CheckInMethod.QR:
        verification_method = VerificationMethod.QR_UPLOAD

    # 7. Create visit session with status=REGISTERED (not CHECKED_IN)
    session_data = VisitSessionCreate(
        tenant_id=tenant_id,
        visitor_profile_id=profile.id,
        department_id=dept_id,
        host_id=host_id,
        receptionist_id=receptionist_id,
        appointment_id=appointment_id,
        privacy_notice_version_id=notice_id,
        check_in_method=request.check_in_method,
        verification_status=verification_status,
        verification_method=verification_method,
        status=VisitStatus.REGISTERED,  # PHASE 1A: Start with REGISTERED, not CHECKED_IN
        purpose=request.purpose,
        visitor_name_snapshot=profile.full_name,
        company_snapshot=profile.company,
        host_name_snapshot=host_name,
        department_name_snapshot=department.name,
        receptionist_name_snapshot=receptionist_name,
        consent_notice_displayed=notice is not None,
        consent_granted=consent_granted,
        consent_method=consent_method,
        consent_timestamp=consent_timestamp_val,
        consent_captured_by_user_id=receptionist_id if consent_granted else None,
        lawful_basis_at_time=lawful_basis,
    )
    session = await create_visit_session(session_data)

    # 8. Update visitor profile visit count and last visit date
    await increment_visitor_profile_visits({"_id": ObjectId(profile.id)})

    # 9. Mark appointment as fulfilled if appointment was linked
    if appointment_id:
        await update_appointment(
            {"_id": ObjectId(appointment_id)},
            AppointmentUpdate(status=AppointmentStatus.FULFILLED),
        )

    # PHASE 1A: Return session + profile only, no badge yet
    return {
        "session": session,
        "visitor_profile": profile,
    }


async def confirm_check_in(
    session_id: str,
    receptionist_id: str,
    tenant_id: str,
    badge_format: str = "A7",
) -> dict:
    """Phase 1A: Confirm check-in — validate minimum fields, generate badge, transition to CHECKED_IN."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    # Ensure status is REGISTERED or PENDING_VERIFICATION
    current_status = session.status if isinstance(session.status, str) else session.status.value
    if current_status not in (VisitStatus.REGISTERED.value, VisitStatus.PENDING_VERIFICATION.value, "registered", "pending_verification"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm check-in with status: {current_status}. Expected REGISTERED or PENDING_VERIFICATION.",
        )

    # Validate minimum required fields
    if not session.visitor_name_snapshot:
        raise HTTPException(status_code=400, detail="Missing required field: visitor_name_snapshot")
    if not session.department_id:
        raise HTTPException(status_code=400, detail="Missing required field: department_id")
    if not session.host_id and not session.purpose:
        raise HTTPException(status_code=400, detail="Missing required fields: either host_id or purpose must be provided")

    # Fetch profile and department for badge generation
    profile = await get_visitor_profile({"_id": ObjectId(session.visitor_profile_id)})
    if not profile:
        raise HTTPException(status_code=404, detail="Visitor profile not found")

    department = await get_department({"_id": ObjectId(session.department_id), "tenant_id": tenant_id})
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Fetch visitor photo if available
    visitor_photo_bytes = None
    if profile and profile.photo_object_key:
        try:
            from core.storage.manager import DocumentStorageManager
            storage_mgr = DocumentStorageManager.get_instance()
            visitor_photo_bytes = await storage_mgr.provider.download_bytes(profile.photo_object_key)
        except Exception:
            pass  # Photo is optional for badge

    # Generate badge with signed QR token
    badge_token = sign_badge_token(session.id, expiry_hours=24)
    now = datetime.now(timezone.utc)
    badge_pdf_bytes = generate_badge_pdf(
        visitor_name=session.visitor_name_snapshot,
        company=session.company_snapshot,
        host_department=f"{session.host_name_snapshot or 'N/A'} / {department.name}",
        date_str=now.strftime("%Y-%m-%d"),
        time_in_str=now.strftime("%H:%M"),
        qr_data=badge_token,
        badge_format=badge_format,
        visitor_photo_bytes=visitor_photo_bytes,
    )

    # Upload badge PDF to storage
    badge_object_key = None
    try:
        from core.storage.manager import DocumentStorageManager
        storage = DocumentStorageManager.get_instance()
        badge_object_key = f"badges/{tenant_id}/{session.id}.pdf"
        await storage.provider.upload_bytes(
            object_key=badge_object_key,
            data=badge_pdf_bytes,
            content_type="application/pdf",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Badge PDF storage failed: %s", e)
        badge_object_key = None

    # Update session: set status to CHECKED_IN, add badge info
    updated_session = await update_visit_session(
        {"_id": ObjectId(session.id)},
        VisitSessionUpdate(
            status=VisitStatus.CHECKED_IN,
            badge_qr_token=badge_token,
            badge_format=badge_format,
            badge_generation_time=int(time.time()),
            badge_expiry=int(time.time()) + 86400,
            badge_pdf_object_key=badge_object_key,
        ),
    )

    # PHASE 1A: Return session + badge info
    return {
        "session": updated_session,
        "badge_pdf_base64": __import__("base64").b64encode(badge_pdf_bytes).decode("utf-8"),
        "badge_qr_token": badge_token,
    }


async def deny_visitor(
    session_id: str,
    reason: str,
    denied_by: str,
    tenant_id: str,
) -> VisitSessionOut:
    """Phase 1B: Deny entry to a registered/pending visitor."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    current_status = session.status if isinstance(session.status, str) else session.status.value
    if current_status not in (VisitStatus.REGISTERED.value, VisitStatus.PENDING_VERIFICATION.value, "registered", "pending_verification"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot deny visitor with status: {current_status}. Can only deny REGISTERED or PENDING_VERIFICATION visitors.",
        )

    updated = await update_visit_session(
        {"_id": ObjectId(session_id)},
        VisitSessionUpdate(
            status=VisitStatus.DENIED,
            denial_reason=reason,
            denied_by=denied_by,
        ),
    )
    return updated


async def retrieve_pending_sessions(tenant_id: str, department_id: str = None, start=0, stop=100):
    """Phase 1C: Get sessions with status in (REGISTERED, PENDING_VERIFICATION)."""
    filter_dict = {
        "tenant_id": tenant_id,
        "status": {"$in": [VisitStatus.REGISTERED.value, VisitStatus.PENDING_VERIFICATION.value]},
    }
    if department_id:
        filter_dict["department_id"] = department_id
    return await get_visit_sessions(filter_dict=filter_dict, start=start, stop=stop)


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


async def _enrich_visit_session(session: VisitSessionOut) -> VisitSessionWithSummaryOut:
    import asyncio
    from services.summary_resolver import (
        resolve_tenant_summary,
        resolve_department_summary,
        resolve_visitor_profile_summary,
        resolve_system_user_summary,
        resolve_appointment_summary,
    )

    (
        tenant_s,
        dept_s,
        visitor_s,
        host_s,
        receptionist_s,
        appointment_s,
        verified_by_s,
        consent_capture_s,
        denied_by_s,
    ) = await asyncio.gather(
        resolve_tenant_summary(session.tenant_id),
        resolve_department_summary(session.department_id),
        resolve_visitor_profile_summary(session.visitor_profile_id),
        resolve_system_user_summary(session.host_id),
        resolve_system_user_summary(session.receptionist_id),
        resolve_appointment_summary(session.appointment_id),
        resolve_system_user_summary(session.verified_by),
        resolve_system_user_summary(session.consent_captured_by_user_id),
        resolve_system_user_summary(session.denied_by),
    )
    data = session.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_s
    data["department_summary"] = dept_s
    data["visitor_profile_summary"] = visitor_s
    data["host_summary"] = host_s
    data["receptionist_summary"] = receptionist_s
    data["appointment_summary"] = appointment_s
    data["verified_by_summary"] = verified_by_s
    data["consent_captured_by_summary"] = consent_capture_s
    data["denied_by_summary"] = denied_by_s
    return VisitSessionWithSummaryOut(**data)


async def retrieve_visit_sessions_with_summary(
    tenant_id: str, department_id: str = None, start: int = 0, stop: int = 100
):
    import asyncio
    sessions = await retrieve_visit_sessions(
        tenant_id=tenant_id, department_id=department_id, start=start, stop=stop
    )
    return list(await asyncio.gather(*[_enrich_visit_session(s) for s in sessions]))


async def retrieve_visit_session_by_id_with_summary(
    session_id: str, tenant_id: str
) -> VisitSessionWithSummaryOut:
    session = await retrieve_visit_session_by_id(session_id=session_id, tenant_id=tenant_id)
    return await _enrich_visit_session(session)


async def verify_id_with_ocr(id_image_object_key: str) -> dict:
    """Run OCR on an ID image and return extracted fields."""
    try:
        from core.ocr.manager import OCRManager
        ocr = OCRManager.get_instance()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="OCR service is not configured")

    try:
        # Download image bytes from storage
        from core.storage.manager import DocumentStorageManager
        storage = DocumentStorageManager.get_instance()
        image_bytes = await storage.provider.download_bytes(id_image_object_key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to retrieve ID image: {str(e)}")

    try:
        result = await ocr.extract_id(image_bytes)
        return {
            "full_name": result.get("full_name"),
            "id_number": result.get("id_number"),
            "id_type": result.get("id_type"),
            "confidence": result.get("confidence", 0.0),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OCR extraction failed: {str(e)}")


async def apply_id_scan_verification(
    session_id: str,
    tenant_id: str,
    id_type: str,
    id_number: str,
    id_image_object_key: str = None,
) -> VisitSessionOut:
    """Apply OCR scan results to a visit session and the linked visitor profile."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    # Update session verification status
    updated_session = await update_visit_session(
        {"_id": ObjectId(session_id)},
        VisitSessionUpdate(
            verification_status=VerificationStatus.VERIFIED,
            verification_method=VerificationMethod.ID_SCAN,
        ),
    )

    # Update visitor profile with ID details and verification state
    if session.visitor_profile_id and ObjectId.is_valid(session.visitor_profile_id):
        profile_update = VisitorProfileUpdate(
            id_type=id_type,
            id_number=id_number,
            verification_status="verified",
            verification_method="id_scan",
        )
        if id_image_object_key:
            profile_update.id_image_object_key = id_image_object_key
        await update_visitor_profile(
            {"_id": ObjectId(session.visitor_profile_id)},
            profile_update,
        )

    # Auto-link OCR vendor as sub-processor (8L)
    try:
        from core.settings import get_settings
        from repositories.sub_processor_repo import get_sub_processor, create_sub_processor
        from schemas.sub_processor_schema import SubProcessorCreate
        settings = get_settings()
        ocr_provider_name = settings.ocr_provider if hasattr(settings, 'ocr_provider') else None
        if ocr_provider_name and ocr_provider_name != "none":
            existing = await get_sub_processor({"tenant_id": tenant_id, "provider": ocr_provider_name})
            if not existing:
                await create_sub_processor(SubProcessorCreate(
                    tenant_id=tenant_id,
                    provider=ocr_provider_name,
                    purpose="Identity document OCR verification",
                    uses_data_for_training=False,
                ))
    except Exception:
        pass  # Non-blocking

    return updated_session


async def approve_visitor_by_host(
    session_id: str,
    host_id: str,
    tenant_id: str,
) -> VisitSessionOut:
    """Host approves a visitor, setting verification to VERIFIED via HOST_APPROVAL."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    # Validate host belongs to tenant
    host = await get_system_user({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if not host:
        raise HTTPException(status_code=404, detail="Host not found in this tenant")

    updated = await update_visit_session(
        {"_id": ObjectId(session_id)},
        VisitSessionUpdate(
            verification_status=VerificationStatus.VERIFIED,
            verification_method=VerificationMethod.HOST_APPROVAL,
            verified_by=host_id,
        ),
    )

    # Update profile-level verification state
    if session.visitor_profile_id and ObjectId.is_valid(session.visitor_profile_id):
        await update_visitor_profile(
            {"_id": ObjectId(session.visitor_profile_id)},
            VisitorProfileUpdate(
                verification_status="verified",
                verification_method="host_approval",
            ),
        )

    return updated


async def generate_tenant_registration_qr(tenant_id: str, department_id: str | None = None, branch_id: str | None = None) -> dict:
    """Generate a signed registration URL/token for visitor QR-based registration."""
    from services.qr_service import sign_registration_token
    token = sign_registration_token(tenant_id, department_id=department_id, branch_id=branch_id)
    registration_url = f"/public/register/{tenant_id}"
    return {
        "registration_url": registration_url,
        "signed_token": token,
        "qr_data": token,
        "tenant_id": tenant_id,
        "department_id": department_id,
        "branch_id": branch_id,
    }


async def download_badge_pdf(session_id: str, tenant_id: str) -> bytes:
    """Download badge PDF bytes from storage."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")
    if not session.badge_pdf_object_key:
        raise HTTPException(status_code=404, detail="Badge PDF not available for this session")
    try:
        from core.storage.manager import DocumentStorageManager
        storage = DocumentStorageManager.get_instance()
        return await storage.provider.download_bytes(session.badge_pdf_object_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to retrieve badge PDF: {str(e)}")


async def resume_draft_registration(
    session_id: str, tenant_id: str, update_data: VisitSessionUpdate
) -> VisitSessionOut:
    """Update a REGISTERED session with additional fields (draft resume)."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_visit_session({"_id": ObjectId(session_id), "tenant_id": tenant_id})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    # Ensure session is in REGISTERED status (draft)
    current_status = session.status if isinstance(session.status, str) else session.status.value
    if current_status not in (VisitStatus.REGISTERED.value, "registered"):
        raise HTTPException(
            status_code=400,
            detail=f"Can only update draft sessions (status: REGISTERED). Current status: {current_status}"
        )

    # Update the session with provided fields
    updated = await update_visit_session(
        {"_id": ObjectId(session_id)},
        update_data,
    )
    return updated
