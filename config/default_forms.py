"""Default tenant form field sets used to seed new form drafts.

The form builder lets a tenant configure what the receptionist /
appointment / kiosk UI captures. To save super_admins from starting
from a blank canvas (and to mirror the system-required field set the
public kiosk already enforces in :mod:`services.checkin_config_service`),
brand-new drafts are pre-populated with the constants below.

The defaults are **drafts**, never published shapes — the super_admin
edits them, then calls ``POST /v1/tenant-forms/{form_id}/publish`` to
make them live. Discarding the draft drops the defaults back to nothing
(an empty published form means the kiosk falls back to the in-code
``DEFAULT_REQUIRED_FIELDS`` set).

Keep this list short. Anything tenant-specific (industry vertical
fields, NDPA consent text, geofencing prompts) belongs in the tenant's
own customisation, not in the system defaults.
"""

from __future__ import annotations

from typing import List

from schemas.imports import FormFieldType, FormTargetType
from schemas.tenant_form_schema import FormFieldDefinition, FormFieldOption


# Default option list for the ``purpose`` select field. Mirrors
# ``services.tenant_enum_service.DEFAULT_PURPOSES_OF_VISIT`` so the
# seeded form lines up with the purpose-of-visit picker the kiosk
# already shows.
_DEFAULT_PURPOSE_OPTIONS: List[FormFieldOption] = [
    FormFieldOption(key="meeting", label="Meeting"),
    FormFieldOption(key="interview", label="Interview"),
    FormFieldOption(key="delivery", label="Delivery"),
    FormFieldOption(key="contractor", label="Contractor / Maintenance"),
    FormFieldOption(key="event", label="Event Attendance"),
    FormFieldOption(key="tour", label="Office Tour"),
    FormFieldOption(key="personal", label="Personal Visit"),
    FormFieldOption(key="other", label="Other"),
]


def _checkin_defaults() -> List[FormFieldDefinition]:
    """Default check-in form fields.

    Mirrors :data:`services.checkin_config_service.DEFAULT_REQUIRED_FIELDS`
    so a fresh tenant gets a form that is identical in shape to what the
    in-code defaults already produce, just editable through the form
    builder.
    """
    return [
        FormFieldDefinition(
            field_id="full_name",
            type=FormFieldType.TEXT,
            label="Full Name",
            required=True,
            order=10,
            maps_to="visitor.full_name",
            min_length=1,
            max_length=120,
            help_text="Visitor's full legal name as it appears on their ID.",
        ),
        FormFieldDefinition(
            field_id="phone",
            type=FormFieldType.PHONE,
            label="Phone Number",
            required=True,
            order=20,
            maps_to="visitor.phone",
            placeholder="+234...",
            help_text="Used to look up returning visitors and for emergency contact.",
        ),
        FormFieldDefinition(
            field_id="email",
            type=FormFieldType.EMAIL,
            label="Email",
            required=False,
            order=30,
            maps_to="visitor.email",
            help_text="Optional — used to email a copy of the visit badge.",
        ),
        FormFieldDefinition(
            field_id="company",
            type=FormFieldType.TEXT,
            label="Company",
            required=False,
            order=40,
            maps_to="visitor.company",
            max_length=160,
        ),
        FormFieldDefinition(
            field_id="purpose",
            type=FormFieldType.SELECT,
            label="Purpose of Visit",
            required=True,
            order=50,
            maps_to="purpose",
            options=list(_DEFAULT_PURPOSE_OPTIONS),
        ),
    ]


def _appointment_defaults() -> List[FormFieldDefinition]:
    """Default appointment form fields.

    Appointments also have *system-required* fields (host_id,
    department_id, scheduled_datetime) — those are enforced by the
    AppointmentCreate schema and are NOT carried in the tenant form
    because they live on first-class columns. Anything in this list is
    the visitor-facing data the super_admin can edit through the form
    builder.
    """
    return [
        FormFieldDefinition(
            field_id="full_name",
            type=FormFieldType.TEXT,
            label="Visitor Full Name",
            required=True,
            order=10,
            maps_to="visitor.full_name",
            min_length=1,
            max_length=120,
        ),
        FormFieldDefinition(
            field_id="phone",
            type=FormFieldType.PHONE,
            label="Visitor Phone",
            required=True,
            order=20,
            maps_to="visitor.phone",
            placeholder="+234...",
        ),
        FormFieldDefinition(
            field_id="email",
            type=FormFieldType.EMAIL,
            label="Visitor Email",
            required=False,
            order=30,
            maps_to="visitor.email",
        ),
        FormFieldDefinition(
            field_id="company",
            type=FormFieldType.TEXT,
            label="Company",
            required=False,
            order=40,
            maps_to="visitor.company",
            max_length=160,
        ),
        FormFieldDefinition(
            field_id="purpose",
            type=FormFieldType.SELECT,
            label="Purpose of Visit",
            required=True,
            order=50,
            maps_to="purpose",
            options=list(_DEFAULT_PURPOSE_OPTIONS),
        ),
        FormFieldDefinition(
            field_id="expected_duration_minutes",
            type=FormFieldType.INTEGER,
            label="Expected Duration (minutes)",
            required=False,
            order=60,
            maps_to="expected_duration_minutes",
            min=5,
            max=480,
            step=5,
            unit="mins",
            help_text="Used to schedule the host and free the meeting room afterwards.",
        ),
    ]


_DEFAULT_NAME_BY_TARGET: dict[str, str] = {
    FormTargetType.CHECKIN.value: "Check-in form",
    FormTargetType.APPOINTMENT.value: "Appointment form",
    FormTargetType.VISIT_SESSION.value: "Visit session form",
}

_DEFAULT_DESCRIPTION_BY_TARGET: dict[str, str] = {
    FormTargetType.CHECKIN.value: (
        "Fields the receptionist (or kiosk) captures when a visitor "
        "arrives. Add tenant-specific fields below the system defaults."
    ),
    FormTargetType.APPOINTMENT.value: (
        "Fields captured when an appointment is created. Host, "
        "department, and scheduled time are system-required and live "
        "on first-class columns — anything below is the visitor-facing "
        "data the tenant configures."
    ),
    FormTargetType.VISIT_SESSION.value: (
        "Fields captured during the visit (badge issuance, escort "
        "assignment, etc)."
    ),
}


def default_fields_for_target(target_type: str) -> List[FormFieldDefinition]:
    """Return a fresh copy of the default field list for a target_type."""
    if target_type == FormTargetType.CHECKIN.value:
        return _checkin_defaults()
    if target_type == FormTargetType.APPOINTMENT.value:
        return _appointment_defaults()
    return []


def default_name_for_target(target_type: str) -> str:
    return _DEFAULT_NAME_BY_TARGET.get(target_type, "Tenant form")


def default_description_for_target(target_type: str) -> str:
    return _DEFAULT_DESCRIPTION_BY_TARGET.get(target_type, "")
