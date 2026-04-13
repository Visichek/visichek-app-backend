from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
import io
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.data_processing_register_repo import get_dpr_entries, create_dpr_entry
from repositories.deletion_log_repo import get_deletion_logs
from schemas.data_processing_register_schema import DPRCreate
from services.compliance_service import get_consent_log, get_consent_log_count, generate_compliance_export

router = APIRouter(prefix="/compliance", tags=["Compliance"])
_compliance_roles = verify_system_user_token("super_admin", "dpo", "auditor")


@router.get("/register")
@document_response(
    message="Data processing register fetched successfully",
    summary="Get data processing register",
    description="Retrieve the complete data processing register (DPR) for compliance documentation.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "field_name": "visitor_email",
        "purpose": "Visitor identification",
        "lawful_basis": "consent",
        "retention_period": "30 days",
        "sub_processor_id": None,
        "crosses_borders": False,
        "date_created": 1712448000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def get_register(principal: AuthPrincipal = Depends(_compliance_roles)):
    return await get_dpr_entries({"tenant_id": principal.tenant_id or ""})


@router.post("/register")
@document_response(
    message="Register entry created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Add data processing register entry",
    description="Create a new entry in the data processing register documenting processing activities.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "field_name": "visitor_email",
        "purpose": "Visitor identification",
        "lawful_basis": "consent",
        "retention_period": "30 days",
        "sub_processor_id": None,
        "crosses_borders": False,
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def add_register_entry(dpr_data: DPRCreate, principal: AuthPrincipal = Depends(_compliance_roles)):
    if principal.tenant_id:
        dpr_data.tenant_id = principal.tenant_id
    return await create_dpr_entry(dpr_data)


@router.get("/deletion-logs")
@document_response(
    message="Deletion logs fetched successfully",
    summary="List deletion logs",
    description="Retrieve audit logs of all data deletion operations performed on the system.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "entity_type": "visitor_profile",
        "entity_id": "visitor_123",
        "reason": "Retention policy expired",
        "action": "anonymise",
        "performed_by": "admin_001",
        "timestamp": 1712448000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Invalid pagination parameters", "code": "VALIDATION_FAILED"}
    }
)
async def list_deletion_logs(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    return await get_deletion_logs({"tenant_id": principal.tenant_id or ""}, start=start, stop=stop)


@router.get("/consent-log")
@document_response(
    message="Consent log retrieved successfully",
    summary="Get consent log for tenant",
    description="Retrieve consent records from visit sessions for NDPA compliance reporting. Supports optional date range filtering and pagination.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "visitor_name_snapshot": "John Doe",
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712448000,
        "consent_captured_by_user_id": "user_123",
        "privacy_notice_version_id": "notice_v1",
        "lawful_basis_at_time": "consent",
        "consent_withdrawal_at": None,
        "department_id": "dept_001",
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Invalid pagination parameters", "code": "VALIDATION_FAILED"}
    }
)
async def get_consent_log_endpoint(
    start_date: Annotated[Optional[int], Query(description="Unix timestamp for start date filter")] = None,
    end_date: Annotated[Optional[int], Query(description="Unix timestamp for end date filter")] = None,
    skip: Annotated[int, Query(ge=0, description="Number of records to skip")] = 0,
    limit: Annotated[int, Query(ge=1, le=1000, description="Maximum records to return")] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    """Retrieve consent records for NDPA compliance reporting.

    Returns consent-related fields from visit sessions with optional:
    - Date range filtering (start_date, end_date as Unix timestamps)
    - Pagination (skip, limit)
    - Department scoping (automatically applied for dept_admin users)
    """
    if not principal.tenant_id:
        return []

    results = await get_consent_log(
        tenant_id=principal.tenant_id,
        principal=principal,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )

    await get_consent_log_count(
        tenant_id=principal.tenant_id,
        principal=principal,
        start_date=start_date,
        end_date=end_date,
    )

    return results


@router.get("/export")
@document_response(
    message="Compliance export generated",
    description="Generate a ZIP file containing all compliance-related data (consent log, deletion log, audit trail, DSR records, retention policies, sub-processor register, data processing register).",
    summary="Download compliance export package",
)
async def compliance_export(
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    """Generate and download compliance export ZIP."""
    zip_bytes = await generate_compliance_export(tenant_id=principal.tenant_id or "")
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=compliance_export.zip"},
    )
