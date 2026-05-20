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


# System-required appointment fields — the ones the platform always
# captures regardless of the tenant's form configuration. These are
# enforced by Pydantic (the AppointmentCreate schema marks them
# non-optional) and surfaced to the frontend via
# :func:`describe_appointment_form_requirements` so the schedule UI
# can split them visually from the tenant-configurable section.
SYSTEM_REQUIRED_APPOINTMENT_FIELDS: tuple[str, ...] = (
    "host_id",
    "department_id",
    "scheduled_datetime",
)


async def _validate_tenant_form_data_for_appointment(
    appt_data: AppointmentCreate,
) -> None:
    """Reject scheduling if any tenant-configured required field is missing.

    Looks up the published tenant_form row with
    ``target_type=appointment`` for the tenant and rejects the create
    when ``appt_data.tenant_form_data`` is missing any field marked
    ``required=True``. Stamps ``tenant_form_id`` / ``tenant_form_version``
    on the appointment so historical rows stay interpretable after the
    super_admin edits or archives the form.
    """
    from core.errors import AppException, ErrorCode
    from repositories.tenant_form_repo import get_active_by_target
    from schemas.imports import FormTargetType

    form = await get_active_by_target(
        tenant_id=appt_data.tenant_id,
        target_type=FormTargetType.APPOINTMENT.value,
    )
    if form is None:
        # No tenant form published — nothing extra to validate. The
        # legacy required fields on AppointmentBase (Pydantic) still
        # enforce host_id / department_id / scheduled_datetime.
        return

    submitted_keys = set(appt_data.tenant_form_data.keys())
    missing: list[str] = []
    for field in form.fields or []:
        if not field.required:
            continue
        value = appt_data.tenant_form_data.get(field.field_id)
        if field.field_id not in submitted_keys or value in (None, "", [], {}):
            missing.append(field.field_id)

    if missing:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                "Cannot schedule appointment: required tenant form "
                f"fields are missing: {', '.join(missing)}"
            ),
            details={
                "missing_fields": missing,
                "tenant_form_id": form.form_id,
                "tenant_form_version": form.version,
            },
        )

    # Snapshot the form id / version that validated this row.
    appt_data.tenant_form_id = form.form_id
    appt_data.tenant_form_version = form.version


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

    # Validate against the published appointment form BEFORE the cap
    # check so a tenant whose form is misconfigured doesn't burn a
    # quota slot on a soon-to-be-rejected create.
    await _validate_tenant_form_data_for_appointment(appt_data)

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

    # Snapshot the host's display name onto the appointment so lists and the
    # appointment-driven check-in flow are self-describing — they never need
    # a live host lookup, and the name survives even if the host (dedicated
    # or system user) is later renamed or removed. host_id may reference the
    # hosts collection (modern) or a system_user (legacy).
    if not (appt_data.host_name_snapshot or "").strip() and appt_data.host_id:
        from services.host_service import resolve_host_identity

        identity = await resolve_host_identity(appt_data.tenant_id, appt_data.host_id)
        if identity is not None and identity[0]:
            appt_data.host_name_snapshot = identity[0]

    return await create_appointment(appt_data, preassigned_id=preassigned_id)


async def describe_appointment_form_requirements(tenant_id: str) -> dict:
    """Return the split system / tenant required-field map for a tenant.

    Backs the ``GET /v1/appointments/form-requirements`` endpoint the
    schedule UI calls to render the form. The system block is static
    (host, department, scheduled_datetime); the tenant block is the
    published TenantForm with ``target_type=appointment`` (or an empty
    list when none is configured).
    """
    from repositories.tenant_form_repo import get_active_by_target
    from schemas.imports import FormTargetType

    form = await get_active_by_target(
        tenant_id=tenant_id, target_type=FormTargetType.APPOINTMENT.value
    )
    return {
        "system_required_fields": [
            {"key": k, "required": True} for k in SYSTEM_REQUIRED_APPOINTMENT_FIELDS
        ],
        "tenant_form_id": form.form_id if form else None,
        "tenant_form_version": form.version if form else None,
        "tenant_required_fields": (
            [
                {
                    "key": f.field_id,
                    "label": f.label or f.field_id,
                    "type": (f.type.value if hasattr(f.type, "value") else str(f.type)),
                    "required": bool(f.required),
                    "placeholder": f.placeholder,
                    "help_text": f.help_text,
                    "options": (
                        [{"key": o.key, "label": o.label} for o in f.options]
                        if f.options
                        else None
                    ),
                }
                for f in (form.fields if form else [])
            ]
            if form
            else []
        ),
    }


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
        resolve_appointment_host_summary,
        resolve_user_summary,
        resolve_visitor_profile_summary,
    )

    tenant_s, dept_s, host_s, visitor_s, creator_s = await asyncio.gather(
        resolve_tenant_summary(appt.tenant_id),
        resolve_department_summary(appt.department_id),
        resolve_appointment_host_summary(appt.host_id),
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
