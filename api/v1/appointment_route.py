from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
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
    describe_appointment_form_requirements,
    retrieve_appointment_by_id_with_summary,
    retrieve_appointments_with_summary,
)
from services.notification_service import (
    extract_resource_ids,
    schedule_resource_read_receipt,
)
from services.visit_session_service import check_in_from_appointment

router = APIRouter(prefix="/appointments", tags=["Appointments"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin", "receptionist")


_APPT_STATUSES = frozenset(
    {"scheduled", "checked_in", "checked_out", "no_show", "cancelled", "fulfilled", "missed"}
)


APPOINTMENTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"scheduled_datetime", "date_created", "status", "last_updated"}
    ),
    default_sort=(("scheduled_datetime", -1),),
    search_fields=("visitor_name_snapshot", "host_name_snapshot", "purpose"),
    filters={
        "status": FilterDef(name="status", multi=True, allowed_values=_APPT_STATUSES),
        "departmentId": FilterDef(name="departmentId", mongo_field="department_id"),
        "hostId": FilterDef(name="hostId", mongo_field="host_id"),
        "branchId": FilterDef(name="branchId", mongo_field="branch_id"),
    },
    range_filters={"scheduledAt": "scheduled_datetime"},
    facet_fields=frozenset({"status"}),
)


def _is_default_appt_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(APPOINTMENTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(APPOINTMENTS_LIST_SPEC.default_limit)


def _map_appt_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _appt_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _APPT_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


@router.get("/form-requirements")
@document_response(
    message="Appointment form requirements retrieved",
    description=(
        "Return the split system + tenant-configured required-field "
        "map for scheduling an appointment. ``system_required_fields`` "
        "is static (host, department, scheduled_datetime) and always "
        "compulsory; ``tenant_required_fields`` comes from the "
        "published TenantForm with ``target_type=appointment`` — every "
        "field marked ``required=True`` must be present in the "
        "``tenant_form_data`` dict on POST /v1/appointments or the "
        "create will be rejected with 400 ``VALIDATION_FAILED``."
    ),
    summary="Get appointment form requirements (split system vs tenant)",
)
async def appointment_form_requirements_endpoint(
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    return await describe_appointment_form_requirements(
        tenant_id=principal.tenant_id or ""
    )


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
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}
    if _is_default_appt_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="appointments.list",
            ttl=60,
            loader=lambda: _load_appointments_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: APPOINTMENTS_LIST_SPEC.default_limit]
        result = {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": APPOINTMENTS_LIST_SPEC.default_limit,
                "hasMore": len(items) > APPOINTMENTS_LIST_SPEC.default_limit,
            },
        }
        _auto_read_appointments(principal, result)
        return result
    query = parse_list_query(request, APPOINTMENTS_LIST_SPEC)
    base_filter: dict[str, Any] = {"tenant_id": tenant_id}
    if principal.is_branch_scoped:
        branch_filter = principal.branch_filter()
        if branch_filter:
            base_filter.update(branch_filter)
    result = await run_list(
        collection=db.expected_appointments,
        query=query,
        base_filter=base_filter,
        map_doc=_map_appt_doc,
        facet_runner=_appt_status_facet,
    )
    _auto_read_appointments(principal, result)
    return result


def _auto_read_appointments(principal: AuthPrincipal, result: Any) -> None:
    """Auto-mark appointment notifications read for ids in this read."""
    schedule_resource_read_receipt(
        user_id=principal.user_id,
        user_role=principal.role,
        resource_type="appointment",
        resource_ids=extract_resource_ids(result),
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
    result = await get_or_compute_entity(
        entity_type="appointment",
        entity_id=appointment_id,
        loader=lambda: retrieve_appointment_by_id_with_summary(
            appointment_id=appointment_id, tenant_id=tenant_id
        ),
    )
    schedule_resource_read_receipt(
        user_id=principal.user_id,
        user_role=principal.role,
        resource_type="appointment",
        resource_ids=[appointment_id],
    )
    return result


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
            "message": (
                "phone is required to check in this appointment — neither the "
                "appointment nor the linked visitor profile has one on record. "
                "Please collect it from the visitor and resubmit this request "
                "with `phone` set."
            ),
            "code": "VALIDATION_FAILED",
            "details": {"missing_field": "phone", "prompt_required": True},
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


# ─── Cancel + bulk endpoints ──────────────────────────────────────────

@router.post("/bulk/cancel")
@document_response(
    message="Bulk cancel queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk cancel appointments",
)
async def bulk_cancel_appointments(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/appointments/bulk/cancel",
        body=payload,
    )
    if hit is not None:
        return hit.response
    extras = {
        "reason": str(payload.get("reason") or "")[:500],
        "tenant_scope": tenant_id,
    }
    response = await enqueue_bulk_write(
        writer_key="appointment.bulk_cancel",
        ids=payload.get("ids", []),
        resource_type="appointment",
        extras=extras,
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/appointments/bulk/cancel",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/delete")
@document_response(
    message="Bulk delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete appointments",
)
async def bulk_delete_appointments(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin")
    ),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/appointments/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="appointment.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="appointment",
        extras={"tenant_scope": tenant_id},
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/appointments/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response

@router.post("/{appointment_id}/cancel")
@document_response(
    message="Appointment cancellation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel appointment (async)",
)
async def cancel_appointment_endpoint(
    appointment_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    request_id = getattr(request.state, "request_id", None)
    enqueue_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "status": "cancelled",
        "_actor_id": principal.user_id,
        "_actor_role": principal.role,
        "_request_id": request_id,
    }
    if "reason" in payload:
        enqueue_payload["cancellation_reason"] = str(payload["reason"])[:500]
    return await enqueue_write(
        writer_key="appointment.update",
        payload=enqueue_payload,
        resource_type="appointment",
        resource_id=appointment_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )
