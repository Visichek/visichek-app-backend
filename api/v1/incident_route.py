from typing import Annotated
from fastapi import APIRouter, Depends, Query, status
from core.response_envelope import document_response
from schemas.incident_log_schema import IncidentLogCreate, IncidentLogUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.incident_service import (
    add_incident, retrieve_incident_by_id, retrieve_incidents, update_incident_by_id,
)

router = APIRouter(prefix="/incidents", tags=["Incidents"])
_security_roles = verify_system_user_token("super_admin", "security_officer")


@router.post("/")
@document_response(
    message="Incident created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new security incident log",
    summary="Create incident",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "tenant_id": "tenant-12345",
        "reported_by": "550e8400-e29b-41d4-a716-446655440000",
        "incident_type": "data_breach",
        "status": "open",
        "description": "Unauthorized access detected in customer database during off-hours",
        "risk_level": "high",
        "data_affected": "customer_names, email_addresses, phone_numbers",
        "mitigation_steps": "Revoked compromised API keys, initiated database audit, notified stakeholders",
        "ndpc_notified": False,
        "ndpc_notified_at": None,
        "detection_time": 1712544600,
        "date_created": 1712544800,
        "resolved_at": None,
    },
    response_codes={
        201: "Incident created successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Unprocessable entity - validation failed",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or missing token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation failed", "code": "VALIDATION_FAILED"},
    },
)
async def create_incident(log_data: IncidentLogCreate, principal: AuthPrincipal = Depends(_security_roles)):
    if principal.tenant_id:
        log_data.tenant_id = principal.tenant_id
    log_data.reported_by = principal.user_id
    return await add_incident(log_data=log_data)


@router.get("/")
@document_response(
    message="Incidents fetched successfully",
    success_example=[
        {
            "id": "6789abcdef0123456789abcd",
            "tenant_id": "tenant-12345",
            "reported_by": "550e8400-e29b-41d4-a716-446655440000",
            "incident_type": "data_breach",
            "status": "open",
            "description": "Unauthorized access detected in customer database during off-hours",
            "risk_level": "high",
            "data_affected": "customer_names, email_addresses, phone_numbers",
            "mitigation_steps": "Revoked compromised API keys, initiated database audit",
            "ndpc_notified": False,
            "ndpc_notified_at": None,
            "detection_time": 1712544600,
            "date_created": 1712544800,
            "resolved_at": None,
        }
    ],
    description="Retrieve a paginated list of security incidents",
    summary="List incidents",
    include_meta=True,
    response_codes={
        200: "Incidents fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or missing token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
    },
)
async def list_incidents(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_security_roles),
):
    return await retrieve_incidents(tenant_id=principal.tenant_id or "", start=start, stop=stop)


@router.get("/{incident_id}")
@document_response(
    message="Incident fetched successfully",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "tenant_id": "tenant-12345",
        "reported_by": "550e8400-e29b-41d4-a716-446655440000",
        "incident_type": "data_breach",
        "status": "investigating",
        "description": "Unauthorized access detected in customer database during off-hours",
        "risk_level": "high",
        "data_affected": "customer_names, email_addresses, phone_numbers",
        "mitigation_steps": "Revoked compromised API keys, initiated database audit, notified stakeholders",
        "ndpc_notified": False,
        "ndpc_notified_at": None,
        "detection_time": 1712544600,
        "date_created": 1712544800,
        "resolved_at": None,
    },
    description="Retrieve a specific incident by ID",
    summary="Get incident",
    response_codes={
        200: "Incident fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Incident not found",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or missing token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Incident not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def get_incident(incident_id: str, principal: AuthPrincipal = Depends(_security_roles)):
    return await retrieve_incident_by_id(incident_id=incident_id, tenant_id=principal.tenant_id or "")


@router.patch("/{incident_id}")
@document_response(
    message="Incident updated successfully",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "tenant_id": "tenant-12345",
        "reported_by": "550e8400-e29b-41d4-a716-446655440000",
        "incident_type": "data_breach",
        "status": "reported_to_ndpc",
        "description": "Unauthorized access detected in customer database during off-hours",
        "risk_level": "high",
        "data_affected": "customer_names, email_addresses, phone_numbers",
        "mitigation_steps": "Revoked compromised API keys, initiated database audit, notified stakeholders, NDPC notified",
        "ndpc_notified": True,
        "ndpc_notified_at": 1712548400,
        "detection_time": 1712544600,
        "date_created": 1712544800,
        "resolved_at": None,
    },
    description="Update an existing security incident",
    summary="Update incident",
    response_codes={
        200: "Incident updated successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Incident not found",
        422: "Unprocessable entity - validation failed",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or missing token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Incident not found", "code": "RESOURCE_NOT_FOUND"},
        422: {"success": False, "message": "Validation failed", "code": "VALIDATION_FAILED"},
    },
)
async def update_incident(
    incident_id: str, log_data: IncidentLogUpdate, principal: AuthPrincipal = Depends(_security_roles),
):
    return await update_incident_by_id(
        incident_id=incident_id, tenant_id=principal.tenant_id or "", log_data=log_data,
    )
