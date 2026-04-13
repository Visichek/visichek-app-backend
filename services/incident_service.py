from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.incident_log_repo import (
    create_incident_log,
    get_incident_log,
    get_incident_logs,
    update_incident_log,
    get_incidents_approaching_deadline,
)
from schemas.incident_log_schema import (
    IncidentLogCreate,
    IncidentLogUpdate,
    IncidentLogOut,
)


async def add_incident(log_data: IncidentLogCreate) -> IncidentLogOut:
    return await create_incident_log(log_data)


async def retrieve_incident_by_id(incident_id: str, tenant_id: str) -> IncidentLogOut:
    if not ObjectId.is_valid(incident_id):
        raise HTTPException(status_code=400, detail="Invalid incident ID format")
    result = await get_incident_log(
        {"_id": ObjectId(incident_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Incident not found")
    return result


async def retrieve_incidents(tenant_id: str, start=0, stop=100) -> List[IncidentLogOut]:
    return await get_incident_logs({"tenant_id": tenant_id}, start=start, stop=stop)


async def update_incident_by_id(
    incident_id: str, tenant_id: str, log_data: IncidentLogUpdate
) -> IncidentLogOut:
    if not ObjectId.is_valid(incident_id):
        raise HTTPException(status_code=400, detail="Invalid incident ID format")
    result = await update_incident_log(
        {"_id": ObjectId(incident_id), "tenant_id": tenant_id}, log_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Incident not found or update failed"
        )
    return result


async def retrieve_incidents_approaching_deadline(
    tenant_id: str, start=0, stop=100
) -> List[IncidentLogOut]:
    """Get incidents where notification deadline is within 24 hours and notification has not been sent."""
    return await get_incidents_approaching_deadline(
        tenant_id=tenant_id, start=start, stop=stop
    )
