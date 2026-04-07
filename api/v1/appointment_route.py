from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.appointment_schema import AppointmentCreate, AppointmentUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.appointment_service import (
    add_appointment,
    retrieve_appointment_by_id,
    retrieve_appointments,
    update_appointment_by_id,
    remove_appointment,
)

router = APIRouter(prefix="/appointments", tags=["Appointments"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin", "receptionist")


@router.post("/")
@document_response(message="Appointment created successfully", status_code=status.HTTP_201_CREATED)
async def create_appointment_endpoint(
    appt_data: AppointmentCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    if principal.tenant_id:
        appt_data.tenant_id = principal.tenant_id
    appt_data.created_by = principal.user_id
    return await add_appointment(appt_data=appt_data)


@router.get("/")
@document_response(message="Appointments fetched successfully", success_example=[])
async def list_appointments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_appointments(tenant_id=tenant_id, start=start, stop=stop)


@router.get("/{appointment_id}")
@document_response(message="Appointment fetched successfully")
async def get_appointment_endpoint(
    appointment_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_appointment_by_id(appointment_id=appointment_id, tenant_id=tenant_id)


@router.patch("/{appointment_id}")
@document_response(message="Appointment updated successfully")
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
@document_response(message="Appointment deleted successfully")
async def delete_appointment_endpoint(
    appointment_id: str,
    principal: AuthPrincipal = Depends(verify_system_user_token("dept_admin", "super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await remove_appointment(appointment_id=appointment_id, tenant_id=tenant_id)
