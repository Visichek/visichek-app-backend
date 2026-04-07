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
@document_response(message="Incident created successfully", status_code=status.HTTP_201_CREATED)
async def create_incident(log_data: IncidentLogCreate, principal: AuthPrincipal = Depends(_security_roles)):
    if principal.tenant_id:
        log_data.tenant_id = principal.tenant_id
    log_data.reporter_id = principal.user_id
    return await add_incident(log_data=log_data)


@router.get("/")
@document_response(message="Incidents fetched successfully", success_example=[])
async def list_incidents(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_security_roles),
):
    return await retrieve_incidents(tenant_id=principal.tenant_id or "", start=start, stop=stop)


@router.get("/{incident_id}")
@document_response(message="Incident fetched successfully")
async def get_incident(incident_id: str, principal: AuthPrincipal = Depends(_security_roles)):
    return await retrieve_incident_by_id(incident_id=incident_id, tenant_id=principal.tenant_id or "")


@router.patch("/{incident_id}")
@document_response(message="Incident updated successfully")
async def update_incident(
    incident_id: str, log_data: IncidentLogUpdate, principal: AuthPrincipal = Depends(_security_roles),
):
    return await update_incident_by_id(
        incident_id=incident_id, tenant_id=principal.tenant_id or "", log_data=log_data,
    )
