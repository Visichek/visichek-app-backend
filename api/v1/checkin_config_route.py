from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.checkin_config_service import (
    create_config,
    delete_config,
    list_configs_for_tenant,
    resolve_public_config,
    update_config,
)
from services.visitor_service import lookup_visitor
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigOut,
    CheckinConfigUpdate,
    PublicCheckinConfigOut,
)

router = APIRouter(prefix="/checkin-configs", tags=["Check-In Configs"])


@router.get("/{checkin_config_id}", response_model=PublicCheckinConfigOut)
@document_response(
    message="Check-in configuration retrieved",
    description="Get public check-in configuration for a kiosk (unauthenticated).",
    summary="Get public check-in config",
    success_example={
        "checkin_config_id": "507f1f77bcf86cd799439012",
        "tenant_id": "t12345",
        "tenant_name": "Acme Corp",
        "logo_url": "https://s3.example.com/logos/acme.png",
        "id_upload_enabled": True,
        "allow_returning_visitor_lookup": True,
        "required_fields": [
            {
                "key": "full_name",
                "label": "Full Name",
                "type": "text",
                "required": True,
                "category": "bio",
            }
        ],
    },
    response_codes={
        404: "Check-in config not found or inactive",
    },
)
async def get_public_checkin_config(checkin_config_id: str):
    """Get public check-in configuration (unauthenticated endpoint for kiosk)."""
    return await resolve_public_config(checkin_config_id)


@router.get("/{checkin_config_id}/visitors/lookup")
@document_response(
    message="Visitor lookup result",
    description="Search for a returning visitor by email and/or phone (kiosk).",
    summary="Lookup visitor",
    success_example={
        "found": True,
        "visitor": {
            "id": "507f1f77bcf86cd799439012",
            "tenant_id": "t12345",
            "full_name": "John Doe",
            "email": "john@example.com",
            "phone": "+1-555-0123",
            "bio_data": {},
            "verified": False,
            "id_number_encrypted": None,
            "id_document_id": None,
            "portrait_url": None,
            "date_created": 1710000000,
            "last_updated": 1710000000,
        },
    },
    response_codes={
        400: "Neither email nor phone provided",
        404: "Visitor not found",
    },
)
async def lookup_returning_visitor(
    checkin_config_id: str,
    email: Annotated[Optional[str], Query()] = None,
    phone: Annotated[Optional[str], Query()] = None,
):
    """Lookup a returning visitor by email and/or phone (kiosk endpoint)."""
    from core.errors import AppException, ErrorCode

    # Validate at least one is provided
    if not email and not phone:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="At least one of email or phone must be provided",
        )

    # Get config to extract tenant_id
    config = await resolve_public_config(checkin_config_id)
    return await lookup_visitor(
        tenant_id=config.tenant_id, email=email, phone=phone
    )


@router.post("/{checkin_config_id}/checkins", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Check-in submitted successfully",
    description="Submit a new check-in via kiosk (unauthenticated).",
    summary="Submit check-in",
    status_code=status.HTTP_201_CREATED,
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "tenant_id": "t12345",
        "visitor_id": "v12345",
        "checkin_config_id": "c12345",
        "id_extraction_id": None,
        "tenant_specific_data": {"department": "Sales"},
        "purpose": {
            "purpose": "Meeting",
            "purpose_details": "Quarterly review",
            "expected_duration_minutes": 60,
        },
        "state": "pending_approval",
        "verified": False,
        "approved_by_user_id": None,
        "approved_at": None,
        "rejection_reason": None,
        "date_created": 1710000000,
        "last_updated": 1710000000,
    },
    response_codes={
        400: "Validation failed or missing required fields",
        409: "Visitor has pending check-in already",
    },
)
async def submit_visitor_checkin(
    checkin_config_id: str,
    payload: CheckinSubmitRequest,
):
    """Submit a check-in via kiosk (unauthenticated endpoint)."""
    from services.checkin_service import submit_checkin as submit_checkin_service

    return await submit_checkin_service(checkin_config_id, payload)


@router.post("", response_model=CheckinConfigOut)
@document_response(
    message="Check-in configuration created",
    description="Create a new check-in configuration (super_admin only).",
    summary="Create check-in config",
    status_code=status.HTTP_201_CREATED,
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - only super_admin allowed",
        404: "Tenant not found",
    },
)
async def create_checkin_config(
    payload: CheckinConfigCreate,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    """Create a new check-in configuration (super_admin only)."""
    return await create_config(payload)


@router.patch("/{checkin_config_id}", response_model=CheckinConfigOut)
@document_response(
    message="Check-in configuration updated",
    description="Update a check-in configuration (super_admin only).",
    summary="Update check-in config",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Config not found",
    },
)
async def update_checkin_config(
    checkin_config_id: str,
    payload: CheckinConfigUpdate,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    """Update a check-in configuration (super_admin only)."""
    return await update_config(checkin_config_id, payload)


@router.get("")
@document_response(
    message="Check-in configurations retrieved",
    description="List check-in configurations for tenant (super_admin/dept_admin).",
    summary="List check-in configs",
    include_meta=True,
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def list_checkin_configs(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("super_admin", "dept_admin")
    ),
):
    """List check-in configurations (super_admin/dept_admin)."""
    tenant_id = principal.tenant_id or ""
    configs, total = await list_configs_for_tenant(
        tenant_id=tenant_id, skip=skip, limit=limit
    )
    return configs, {"total": total, "skip": skip, "limit": limit}
