from __future__ import annotations

import secrets
import time
from bson import ObjectId
from typing import Optional
from fastapi import HTTPException

from core.database import db
from repositories.visitor_profile_repo import (
    get_visitor_profile,
    get_visitor_profile_by_phone,
    update_visitor_profile,
)
from repositories.data_subject_request_repo import create_dsr, get_dsr
from schemas.data_subject_request_schema import DSRCreate
from schemas.visitor_profile_schema import VisitorProfileUpdate
from schemas.imports import DSRType, DSRStatus, ProfilingPreference

VERIFICATION_TOKEN_COLLECTION = "dsr_verification_tokens"


async def _find_visitor_profile(
    tenant_id: str, phone: Optional[str] = None, email: Optional[str] = None
):
    """Look up a visitor profile by phone or email."""
    if not phone and not email:
        raise HTTPException(
            status_code=400,
            detail="Must provide phone or email for identity verification",
        )

    profile = None
    if phone:
        profile = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
    if not profile and email:
        profile = await get_visitor_profile(
            {"tenant_id": tenant_id, "email_address": email}
        )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Visitor profile not found for the provided contact info",
        )
    return profile


async def submit_data_subject_request(request) -> dict:
    """Create a DSR record with verification token and 30-day SLA deadline."""
    profile = await _find_visitor_profile(
        request.tenant_id, request.phone, getattr(request, "email", None)
    )

    verification_token = secrets.token_urlsafe(32)
    sla_deadline = int(time.time()) + (30 * 24 * 3600)  # 30 days

    dsr = DSRCreate(
        tenant_id=request.tenant_id,
        visitor_profile_id=profile.id,
        request_type=request.request_type,
        status=DSRStatus.PENDING,
        sla_deadline=sla_deadline,
        notes=getattr(request, "details", None),
    )
    created = await create_dsr(dsr)

    # Store verification token
    await db[VERIFICATION_TOKEN_COLLECTION].insert_one(
        {
            "dsr_id": created.id,
            "token": verification_token,
            "created_at": int(time.time()),
        }
    )

    return {
        "request_id": created.id,
        "status": created.status,
        "verification_token": verification_token,
        "due_date": sla_deadline,
        "message": "Your request has been submitted. Use the verification token to check status.",
    }


async def check_request_status(request_id: str, verification_token: str) -> dict:
    """Check DSR status by ID and verification token."""
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status_code=400, detail="Invalid request ID")

    token_record = await db[VERIFICATION_TOKEN_COLLECTION].find_one(
        {
            "dsr_id": request_id,
            "token": verification_token,
        }
    )
    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid verification token")

    dsr = await get_dsr({"_id": ObjectId(request_id)})
    if not dsr:
        raise HTTPException(status_code=404, detail="Request not found")

    return {
        "request_id": dsr.id,
        "status": dsr.status,
        "request_type": dsr.request_type,
        "due_date": dsr.sla_deadline,
        "received_at": dsr.received_at,
        "resolved_at": getattr(dsr, "resolved_at", None),
    }


async def withdraw_visitor_consent(request) -> dict:
    """Withdraw consent for all active sessions belonging to the visitor."""
    profile = await _find_visitor_profile(
        request.tenant_id, request.phone, getattr(request, "email", None)
    )

    now = int(time.time())
    result = await db.visit_sessions.update_many(
        {
            "visitor_profile_id": profile.id,
            "tenant_id": request.tenant_id,
            "consent_withdrawal_at": None,
        },
        {"$set": {"consent_withdrawal_at": now, "last_updated": now}},
    )

    # Create DSR record
    dsr = DSRCreate(
        tenant_id=request.tenant_id,
        visitor_profile_id=profile.id,
        request_type=DSRType.CONSENT_WITHDRAWAL,
        status=DSRStatus.COMPLETED,
        sla_deadline=int(time.time()) + (30 * 24 * 3600),
        notes="Consent withdrawal via public portal",
    )
    await create_dsr(dsr)

    return {
        "message": "Consent withdrawal recorded",
        "sessions_updated": result.modified_count,
    }


async def opt_out_profiling(request) -> dict:
    """Opt visitor out of repeat visitor profiling."""
    profile = await _find_visitor_profile(
        request.tenant_id, request.phone, getattr(request, "email", None)
    )

    await update_visitor_profile(
        {"_id": ObjectId(profile.id)},
        VisitorProfileUpdate(profiling_preference=ProfilingPreference.OPTED_OUT),
    )

    return {"message": "Profiling preference updated to opted_out"}
