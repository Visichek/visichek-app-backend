from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
)
from schemas.visit_session_schema import AppointmentCheckInRequest
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.appointment_service import (
    retrieve_appointment_by_id_with_summary,
    retrieve_appointments_with_summary,
)
from services.visit_session_service import check_in_from_appointment

router = APIRouter(prefix="/appointments", tags=["Appointments"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin", "receptionist")


@router.post("")
@document_response(
    message="Appointment creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue an appointment create. The ID is pre-assigned so the client can poll the list view.",
    summary="Create new appointment (async)",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Validation error",
    },
)
async def create_appointment_endpoint(
    appt_data: AppointmentCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    payload = appt_data.model_dump(exclude_none=True)
    if principal.tenant_id:
        payload["tenant_id"] = principal.tenant_id
    payload["created_by"] = principal.user_id
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="appointment.create",
        payload=payload,
        resource_type="appointment",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.get("")
@document_response(
    message="Appointments fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List appointments with pagination",
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
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="appointments.list",
            ttl=60,
            loader=lambda: _load_appointments_for_tenant(tenant_id),
        )
    return await retrieve_appointments_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
    )


async def _load_appointments_for_tenant(tenant_id: str) -> List[Any]:
    appts = await retrieve_appointments_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [
        a.model_dump(mode="json", by_alias=True) if hasattr(a, "model_dump") else a
        for a in appts
    ]


@router.get("/{appointment_id}")
@document_response(
    message="Appointment fetched successfully",
    description="Retrieve detailed information about a specific appointment.",
    summary="Fetch appointment by ID",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Appointment not found",
    },
)
async def get_appointment_endpoint(
    appointment_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="appointment",
        entity_id=appointment_id,
        loader=lambda: retrieve_appointment_by_id_with_summary(
            appointment_id=appointment_id, tenant_id=tenant_id
        ),
    )


@router.patch("/{appointment_id}")
@document_response(
    message="Appointment update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial appointment update.",
    summary="Update appointment (async)",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Validation error",
    },
)
async def update_appointment_endpoint(
    appointment_id: str,
    appt_data: AppointmentUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = appt_data.model_dump(mode="json", exclude_none=True)
    payload["tenant_id"] = tenant_id
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="appointment.update",
        payload=payload,
        resource_type="appointment",
        resource_id=appointment_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.post("/{appointment_id}/check-in")
@document_response(
    message="Visitor checked in from appointment",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Convert a SCHEDULED appointment into a real visit-session check-in. "
        "Hydrates the visitor identity from the appointment's snapshot + "
        "linked visitor profile, then runs the standard register → confirm "
        "flow. The appointment is moved to CHECKED_IN as a side-effect of "
        "the badge being issued. Set ``issue_badge=false`` to register the "
        "visitor without printing a badge yet (e.g. when KYC needs to "
        "complete first); the appointment then stays SCHEDULED until "
        "confirm_check_in fires."
    ),
    summary="Check in a scheduled appointment",
    success_example={
        "appointment_id": "69fcc9d3e1c9a86e47bc7564",
        "session": {
            "id": "507f1f77bcf86cd799439011",
            "status": "checked_in",
            "appointment_id": "69fcc9d3e1c9a86e47bc7564",
            "visitor_name_snapshot": "Edoka Issac",
            "host_name_snapshot": "Nathaniel Uriri",
            "department_name_snapshot": "Human Resources",
            "check_in_time": 1778952010,
            "badge_qr_token": "VIS_20260515_1234567890AB",
        },
        "visitor_profile": {
            "id": "507f1f77bcf86cd799439012",
            "phone": "+2341232323432",
            "full_name": "Edoka Issac",
        },
        "badge_qr_token": "VIS_20260515_1234567890AB",
        "badge_pdf_base64": "JVBERi0xLjQK...",
    },
    response_codes={
        400: "Appointment is in a terminal state, or required visitor data is missing",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Appointment not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "phone is required to look up or create the visitor profile",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Appointment not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def check_in_appointment_endpoint(
    appointment_id: str,
    request: AppointmentCheckInRequest,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    badge_format_value = (
        request.badge_format.value
        if request.badge_format is not None
        else "A7"
    )
    return await check_in_from_appointment(
        appointment_id=appointment_id,
        tenant_id=tenant_id,
        receptionist_id=principal.user_id,
        phone=request.phone,
        full_name=request.full_name,
        company=request.company,
        photo_object_key=request.photo_object_key,
        id_image_object_key=request.id_image_object_key,
        consent_granted=request.consent_granted,
        badge_format=badge_format_value,
        issue_badge=request.issue_badge,
    )


@router.delete("/{appointment_id}")
@document_response(
    message="Appointment deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue an appointment deletion. Only dept_admin / super_admin may call.",
    summary="Delete appointment (async)",
    success_example={
        "id": "507f1f77bcf86cd799439013",
        "job_id": "c4e6f8a0-3456-4fab-9bcd-2345678901cd",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def delete_appointment_endpoint(
    appointment_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin")
    ),
):
    tenant_id = principal.tenant_id or ""
    request_id = getattr(request.state, "request_id", None)
    payload = {
        "tenant_id": tenant_id,
        "_actor_id": principal.user_id,
        "_actor_role": principal.role,
        "_request_id": request_id,
    }
    return await enqueue_write(
        writer_key="appointment.delete",
        payload=payload,
        resource_type="appointment",
        resource_id=appointment_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )
