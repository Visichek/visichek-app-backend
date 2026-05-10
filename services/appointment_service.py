import time
from bson import ObjectId
from fastapi import HTTPException
from typing import Any, Dict, List, Optional, Tuple

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
from services.plan_limits import (
    enforce_entity_cap,
    enforce_feature_enabled,
    get_month_bounds,
)


async def add_appointment(
    appt_data: AppointmentCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> AppointmentOut:
    # Hard feature gate — appointments are denied on Free / Starter via
    # plan ``feature_rules``. This raises 403 with a clear message
    # ("Your plan does not include appointments.") instead of letting
    # the call fall through to the cap check (which on Free returns the
    # less helpful "Monthly appointment limit reached (0)").
    await enforce_feature_enabled(
        tenant_id=appt_data.tenant_id,
        endpoint_pattern="/v1/appointments",
        method="POST",
        friendly_name="appointments",
    )

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


def _resolve_appointment_photo_url(object_key: Optional[str]) -> Optional[str]:
    """Best-effort presigned URL for the host-uploaded visitor photo.

    Returns ``None`` silently if storage is misconfigured — appointments
    must remain readable even when the storage backend is offline."""
    if not object_key:
        return None
    try:
        from core.storage.manager import DocumentStorageManager

        return DocumentStorageManager.get_instance().provider.download_url(
            object_key=object_key
        )
    except Exception:
        return None


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
    # Surface the host-uploaded photo as a presigned URL so the
    # receptionist UI doesn't need a second roundtrip through storage.
    data["expected_visitor_photo_url"] = _resolve_appointment_photo_url(
        appt.expected_visitor_photo_object_key
    )
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


_AUDITABLE_UPDATE_FIELDS = (
    "visitor_profile_id",
    "status",
    "scheduled_datetime",
    "purpose",
)


def _diff_appointment(
    before: AppointmentOut, after: AppointmentOut
) -> Dict[str, Dict[str, Any]]:
    """Return a {field: {before, after}} diff for audit-log details."""
    changes: Dict[str, Dict[str, Any]] = {}
    for field in _AUDITABLE_UPDATE_FIELDS:
        old_val: Any = getattr(before, field, None)
        new_val: Any = getattr(after, field, None)
        if old_val != new_val:
            changes[field] = {
                "before": getattr(old_val, "value", old_val),
                "after": getattr(new_val, "value", new_val),
            }
    return changes


async def update_appointment_by_id_with_diff(
    appointment_id: str,
    tenant_id: str,
    appt_data: AppointmentUpdate,
) -> Tuple[AppointmentOut, AppointmentOut, Dict[str, Dict[str, Any]]]:
    """Apply a partial update and return ``(before, after, changes)``.

    Rejects updates to appointments whose scheduled time is already in the
    past, and rejects rescheduling to a past datetime. Used by the writer
    to produce audit-log diffs without re-fetching the document.
    """
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")

    existing = await get_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Appointment not found")

    now_ts = int(time.time())
    if existing.scheduled_datetime is not None and existing.scheduled_datetime < now_ts:
        raise HTTPException(
            status_code=400,
            detail="Cannot update an appointment whose scheduled date has already passed",
        )
    if (
        appt_data.scheduled_datetime is not None
        and appt_data.scheduled_datetime < now_ts
    ):
        raise HTTPException(
            status_code=400,
            detail="Cannot reschedule an appointment to a past datetime",
        )

    result = await update_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}, appt_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Appointment not found or update failed"
        )
    return existing, result, _diff_appointment(existing, result)


async def update_appointment_by_id(
    appointment_id: str, tenant_id: str, appt_data: AppointmentUpdate
) -> AppointmentOut:
    _, after, _ = await update_appointment_by_id_with_diff(
        appointment_id=appointment_id,
        tenant_id=tenant_id,
        appt_data=appt_data,
    )
    return after


async def remove_appointment(appointment_id: str, tenant_id: str):
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID format")
    result = await delete_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Appointment not found")
