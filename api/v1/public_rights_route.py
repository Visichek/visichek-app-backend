from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, status
from pydantic import BaseModel, EmailStr

from core.response_envelope import document_response
from schemas.imports import DSRType

router = APIRouter(prefix="/public/rights", tags=["Public Visitor Rights"])


class PublicDSRRequest(BaseModel):
    """Visitor submits a data subject request (access, correction, deletion, consent withdrawal)."""
    request_type: DSRType
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    tenant_id: str
    details: Optional[str] = None


class ConsentWithdrawalRequest(BaseModel):
    """Visitor withdraws consent."""
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    tenant_id: str


class ProfilingOptOutRequest(BaseModel):
    """Visitor opts out of repeat visitor profiling."""
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    tenant_id: str


@router.post("/request")
@document_response(
    message="Data subject request submitted",
    status_code=status.HTTP_201_CREATED,
    description="Submit a data subject request (access, correction, deletion, consent withdrawal). No authentication required.",
    summary="Submit data subject request",
    success_example={
        "request_id": "507f1f77bcf86cd799439011",
        "status": "pending",
        "verification_token": "abc123def456",
        "due_date": 1715000000,
        "message": "Your request has been submitted. Use the verification token to check status.",
    },
    response_codes={400: "Must provide phone or email", 404: "Visitor profile not found"},
)
async def submit_dsr(request: PublicDSRRequest):
    """Public DSR submission. No auth required."""
    from services.public_rights_service import submit_data_subject_request
    return await submit_data_subject_request(request)


@router.get("/request/{request_id}/status")
@document_response(
    message="Request status retrieved",
    description="Check the status of a data subject request using the verification token.",
    summary="Check DSR status",
    success_example={"request_id": "507f1f77bcf86cd799439011", "status": "pending", "due_date": 1715000000},
    response_codes={400: "Invalid verification token", 404: "Request not found"},
)
async def check_dsr_status(request_id: str, verification_token: str):
    """Check DSR status with verification token. No auth required."""
    from services.public_rights_service import check_request_status
    return await check_request_status(request_id, verification_token)


@router.post("/withdraw-consent")
@document_response(
    message="Consent withdrawal submitted",
    status_code=status.HTTP_200_OK,
    description="Visitor withdraws consent for data processing. Sets consent_withdrawal_at on all active sessions.",
    summary="Withdraw consent",
    success_example={"message": "Consent withdrawal recorded", "sessions_updated": 3},
    response_codes={400: "Must provide phone or email", 404: "Visitor profile not found"},
)
async def withdraw_consent(request: ConsentWithdrawalRequest):
    """Public consent withdrawal. No auth required."""
    from services.public_rights_service import withdraw_visitor_consent
    return await withdraw_visitor_consent(request)


@router.patch("/profiling-opt-out")
@document_response(
    message="Profiling opt-out recorded",
    description="Visitor opts out of repeat visitor profiling.",
    summary="Opt out of visitor profiling",
    success_example={"message": "Profiling preference updated to opted_out"},
    response_codes={400: "Must provide phone or email", 404: "Visitor profile not found"},
)
async def profiling_opt_out(request: ProfilingOptOutRequest):
    """Public profiling opt-out. No auth required."""
    from services.public_rights_service import opt_out_profiling
    return await opt_out_profiling(request)
