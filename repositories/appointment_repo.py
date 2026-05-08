from bson import ObjectId
from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentOut,
)


async def create_appointment(
    appt_data: AppointmentCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> AppointmentOut:
    appt_dict = appt_data.model_dump()
    if preassigned_id:
        appt_dict["_id"] = ObjectId(preassigned_id)
    result = await db.expected_appointments.insert_one(appt_dict)
    result = await db.expected_appointments.find_one({"_id": result.inserted_id})
    return AppointmentOut(**result)


async def get_appointment(filter_dict: dict) -> Optional[AppointmentOut]:
    try:
        result = await db.expected_appointments.find_one(filter_dict)
        if result is None:
            return None
        return AppointmentOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching appointment: {str(e)}",
        )


async def get_appointments(
    filter_dict: dict = {}, start=0, stop=100
) -> List[AppointmentOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = (
            db.expected_appointments.find(filter_dict)
            .sort("scheduled_datetime", -1)
            .skip(start)
            .limit(stop - start)
        )
        appt_list = []
        async for doc in cursor:
            appt_list.append(AppointmentOut(**doc))
        return appt_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching appointments: {str(e)}",
        )


async def update_appointment(
    filter_dict: dict, appt_data: AppointmentUpdate
) -> AppointmentOut:
    update_dict = {k: v for k, v in appt_data.model_dump().items() if v is not None}
    result = await db.expected_appointments.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return AppointmentOut(**result)


async def delete_appointment(filter_dict: dict):
    return await db.expected_appointments.delete_one(filter_dict)


async def count_appointments(filter_dict: dict | None = None) -> int:
    if filter_dict is None:
        filter_dict = {}
    return await db.expected_appointments.count_documents(filter_dict)


def _due_scheduled_checkout_filter(
    tenant_id: str,
    due_before_ts: int,
    department_id: Optional[str] = None,
) -> dict:
    filter_dict: dict = {
        "tenant_id": tenant_id,
        "status": "scheduled",
        "scheduled_datetime": {"$lt": due_before_ts},
    }
    if department_id:
        filter_dict["department_id"] = department_id
    return filter_dict


async def get_due_scheduled_appointments_for_checkout(
    tenant_id: str,
    due_before_ts: int,
    department_id: Optional[str] = None,
    start: int = 0,
    stop: int = 50,
) -> List[AppointmentOut]:
    """Scheduled appointments whose scheduled day is today or earlier."""
    return await get_appointments(
        filter_dict=_due_scheduled_checkout_filter(
            tenant_id, due_before_ts, department_id
        ),
        start=start,
        stop=stop,
    )


async def count_due_scheduled_appointments_for_checkout(
    tenant_id: str,
    due_before_ts: int,
    department_id: Optional[str] = None,
) -> int:
    """Count scheduled appointments whose scheduled day is today or earlier."""
    return await count_appointments(
        _due_scheduled_checkout_filter(tenant_id, due_before_ts, department_id)
    )
