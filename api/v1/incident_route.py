from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.incident_log_schema import IncidentLogCreateRequest, IncidentLogUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.incident_service import (
    retrieve_incident_by_id,
    retrieve_incidents,
    retrieve_incidents_approaching_deadline,
)

router = APIRouter(prefix="/incidents", tags=["Incidents"])
_security_roles = verify_system_user_token("super_admin", "security_officer")


@router.post("")
@document_response(
    message="Incident creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a security incident creation.",
    summary="Create incident (async)",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Unprocessable entity - validation failed",
    },
)
async def create_incident(
    log_data: IncidentLogCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_security_roles),
):
    payload = log_data.model_dump(exclude_none=True)
    payload["tenant_id"] = principal.tenant_id or ""
    payload["reported_by"] = principal.user_id
    return await enqueue_write(
        writer_key="incident.create",
        payload=payload,
        resource_type="incident",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Incidents fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List incidents",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_incidents(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_security_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="incidents.list",
            ttl=60,
            loader=lambda: _load_incidents_for_tenant(tenant_id),
        )
    return await retrieve_incidents(tenant_id=tenant_id, start=start, stop=stop)


async def _load_incidents_for_tenant(tenant_id: str) -> List[Any]:
    incidents = await retrieve_incidents(tenant_id=tenant_id, start=0, stop=100)
    return [
        i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
        for i in incidents
    ]


@router.get("/approaching-deadline")
@document_response(
    message="Incidents approaching notification deadline fetched",
    description="Served from the per-tenant precompute cache.",
    summary="List incidents approaching 72h NDPC notification deadline",
    include_meta=True,
)
async def get_approaching_deadline_incidents(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_security_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="incidents.approaching_deadline",
            ttl=60,
            loader=lambda: _load_approaching_deadline(tenant_id),
        )
    return await retrieve_incidents_approaching_deadline(
        tenant_id=tenant_id, start=start, stop=stop
    )


async def _load_approaching_deadline(tenant_id: str) -> List[Any]:
    incidents = await retrieve_incidents_approaching_deadline(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [
        i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
        for i in incidents
    ]


@router.get("/{incident_id}")
@document_response(
    message="Incident fetched successfully",
    description="Retrieve a specific incident by ID.",
    summary="Get incident",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Incident not found",
    },
)
async def get_incident(
    incident_id: str, principal: AuthPrincipal = Depends(_security_roles)
):
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="incident",
        entity_id=incident_id,
        loader=lambda: retrieve_incident_by_id(
            incident_id=incident_id, tenant_id=tenant_id
        ),
    )


@router.patch("/{incident_id}")
@document_response(
    message="Incident update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial incident update.",
    summary="Update incident (async)",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Unprocessable entity - validation failed",
    },
)
async def update_incident(
    incident_id: str,
    log_data: IncidentLogUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_security_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = log_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="incident.update",
        payload=payload,
        resource_type="incident",
        resource_id=incident_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
