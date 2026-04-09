from typing import Annotated, List

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentWithSummaryOut,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.appointment_service import (
    add_appointment,
    retrieve_appointment_by_id,
    retrieve_appointment_by_id_with_summary,
    retrieve_appointments,
    retrieve_appointments_with_summary,
    update_appointment_by_id,
    remove_appointment,
)

router = APIRouter(prefix="/appointments", tags=["Appointments"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin", "receptionist")


@router.post("/")
@document_response(
    message="Appointment created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new visitor appointment with date, time, and host information.",
    summary="Create new appointment",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "host_id": "h12345",
        "department_id": "d12345",
        "visitor_name_snapshot": "John Doe",
        "host_name_snapshot": "Jane Smith",
        "scheduled_datetime": 1712618400,
        "purpose": "Sales consultation",
        "status": "scheduled",
        "created_by": "r12345",
        "date_created": 1712532000,
        "last_updated": 1712532000,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Host or department not found",
        409: "Scheduling conflict - requested time slot unavailable",
        422: "Validation error - invalid appointment data",
    },
    error_examples={
        409: {"success": False, "message": "Scheduling conflict at requested time", "code": "CONFLICT"},
        422: {"success": False, "message": "Invalid appointment data", "code": "VALIDATION_FAILED"},
    },
)
async def create_appointment_endpoint(
    appt_data: AppointmentCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    if principal.tenant_id:
        appt_data.tenant_id = principal.tenant_id
    appt_data.created_by = principal.user_id
    return await add_appointment(appt_data=appt_data)


@router.get("/")
@document_response(
    message="Appointments fetched successfully",
    description="Retrieve paginated list of appointments for the current tenant.",
    summary="List appointments with pagination",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439013",
            "tenant_id": "t12345",
            "visitor_profile_id": "507f1f77bcf86cd799439012",
            "host_id": "h12345",
            "department_id": "d12345",
            "visitor_name_snapshot": "John Doe",
            "host_name_snapshot": "Jane Smith",
            "scheduled_datetime": 1712618400,
            "purpose": "Sales consultation",
            "status": "scheduled",
            "created_by": "r12345",
            "date_created": 1712532000,
            "last_updated": 1712532000,
        }
    ],
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_appointments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> List[AppointmentWithSummaryOut]:
    tenant_id = principal.tenant_id or ""
    return await retrieve_appointments_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
    )


@router.get("/{appointment_id}")
@document_response(
    message="Appointment fetched successfully",
    description="Retrieve detailed information about a specific appointment.",
    summary="Fetch appointment by ID",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "host_id": "h12345",
        "department_id": "d12345",
        "visitor_name_snapshot": "John Doe",
        "host_name_snapshot": "Jane Smith",
        "scheduled_datetime": 1712618400,
        "purpose": "Sales consultation",
        "status": "scheduled",
        "created_by": "r12345",
        "date_created": 1712532000,
        "last_updated": 1712532000,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Appointment not found",
    },
    error_examples={
        404: {"success": False, "message": "Appointment not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def get_appointment_endpoint(
    appointment_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> AppointmentWithSummaryOut:
    tenant_id = principal.tenant_id or ""
    return await retrieve_appointment_by_id_with_summary(
        appointment_id=appointment_id, tenant_id=tenant_id
    )


@router.patch("/{appointment_id}")
@document_response(
    message="Appointment updated successfully",
    description="Update appointment details such as date, time, or host information.",
    summary="Update appointment",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "host_id": "h12345",
        "department_id": "d12345",
        "visitor_name_snapshot": "John Doe",
        "host_name_snapshot": "Jane Smith",
        "scheduled_datetime": 1712704800,
        "purpose": "Sales consultation",
        "status": "scheduled",
        "created_by": "r12345",
        "date_created": 1712532000,
        "last_updated": 1712535600,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Appointment not found",
        409: "Scheduling conflict - new time slot unavailable",
    },
    error_examples={
        404: {"success": False, "message": "Appointment not found", "code": "RESOURCE_NOT_FOUND"},
        409: {"success": False, "message": "Scheduling conflict at requested time", "code": "CONFLICT"},
    },
)
async def update_appointment_endpoint(
    appointment_id: str,
    appt_data: AppointmentUpdate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await update_appointment_by_id(
        appointment_id=appointment_id, tenant_id=tenant_id, appt_data=appt_data
    )


@router.delete("/{appointment_id}")
@document_response(
    message="Appointment deleted successfully",
    description="Delete an appointment by ID. Only dept_admin and super_admin can delete.",
    summary="Delete appointment",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "host_id": "h12345",
        "department_id": "d12345",
        "visitor_name_snapshot": "John Doe",
        "host_name_snapshot": "Jane Smith",
        "scheduled_datetime": 1712618400,
        "purpose": "Sales consultation",
        "status": "scheduled",
        "created_by": "r12345",
        "date_created": 1712532000,
        "last_updated": 1712535600,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Appointment not found",
    },
    error_examples={
        404: {"success": False, "message": "Appointment not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def delete_appointment_endpoint(
    appointment_id: str,
    principal: AuthPrincipal = Depends(verify_system_user_token("dept_admin", "super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await remove_appointment(appointment_id=appointment_id, tenant_id=tenant_id)
