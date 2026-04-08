from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.appointment_repo import (
    create_appointment,
    get_appointment,
    get_appointments,
    update_appointment,
    delete_appointment,
)
from schemas.appointment_schema import AppointmentCreate, AppointmentUpdate, AppointmentOut


async def add_appointment(appt_data: AppointmentCreate) -> AppointmentOut:
    return await create_appointment(appt_data)


async def retrieve_appointment_by_id(appointment_id: str, tenant_id: str) -> AppointmentOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await get_appointment({"_id": ObjectId(appointment_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return result


async def retrieve_appointments(tenant_id: str, start=0, stop=100) -> List[AppointmentOut]:
    return await get_appointments(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def update_appointment_by_id(
    appointment_id: str, tenant_id: str, appt_data: AppointmentUpdate
) -> AppointmentOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await update_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}, appt_data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found or update failed")
    return result


async def remove_appointment(appointment_id: str, tenant_id: str):
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await delete_appointment({"_id": ObjectId(appointment_id), "tenant_id": tenant_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Appointment not found")
