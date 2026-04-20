from bson import ObjectId
from fastapi import HTTPException
from typing import List, Optional

from repositories.appointment_repo import (
    count_appointments,
    create_appointment,
    get_appointment,
    get_appointments,
    update_appointment,
    delete_appointment,
)
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentOut,
    AppointmentWithSummaryOut,
)
from services.plan_limits import enforce_entity_cap, get_month_bounds


async def add_appointment(
    appt_data: AppointmentCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> AppointmentOut:
    # Enforce plan cap on appointments created this calendar month
    month_start, month_end = get_month_bounds()
    month_count = await count_appointments(
        {
            "tenant_id": appt_data.tenant_id,
            "date_created": {"$gte": month_start, "$lt": month_end},
        }
    )
    await enforce_entity_cap(
        tenant_id=appt_data.tenant_id,
        cap_key="max_appointments_per_month",
        current_count=month_count,
        friendly_name="Monthly appointment",
    )

    return await create_appointment(appt_data, preassigned_id=preassigned_id)


async def retrieve_appointment_by_id(
    appointment_id: str, tenant_id: str
) -> AppointmentOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await get_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return result


async def retrieve_appointments(
    tenant_id: str, start=0, stop=100
) -> List[AppointmentOut]:
    return await get_appointments(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def _enrich_appointment(appt: AppointmentOut) -> AppointmentWithSummaryOut:
    import asyncio
    from services.summary_resolver import (
        resolve_tenant_summary,
        resolve_department_summary,
        resolve_system_user_summary,
        resolve_user_summary,
        resolve_visitor_profile_summary,
    )

    tenant_s, dept_s, host_s, visitor_s, creator_s = await asyncio.gather(
        resolve_tenant_summary(appt.tenant_id),
        resolve_department_summary(appt.department_id),
        resolve_system_user_summary(appt.host_id),
        resolve_visitor_profile_summary(appt.visitor_profile_id),
        resolve_user_summary(appt.created_by),
    )
    data = appt.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_s
    data["department_summary"] = dept_s
    data["host_summary"] = host_s
    data["visitor_profile_summary"] = visitor_s
    data["created_by_summary"] = creator_s
    return AppointmentWithSummaryOut(**data)


async def retrieve_appointments_with_summary(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[AppointmentWithSummaryOut]:
    import asyncio

    appts = await retrieve_appointments(tenant_id=tenant_id, start=start, stop=stop)
    return list(await asyncio.gather(*[_enrich_appointment(a) for a in appts]))


async def retrieve_appointment_by_id_with_summary(
    appointment_id: str, tenant_id: str
) -> AppointmentWithSummaryOut:
    appt = await retrieve_appointment_by_id(
        appointment_id=appointment_id, tenant_id=tenant_id
    )
    return await _enrich_appointment(appt)


async def update_appointment_by_id(
    appointment_id: str, tenant_id: str, appt_data: AppointmentUpdate
) -> AppointmentOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await update_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}, appt_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Appointment not found or update failed"
        )
    return result


async def remove_appointment(appointment_id: str, tenant_id: str):
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await delete_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Appointment not found")
