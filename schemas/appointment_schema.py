from schemas.imports import *
from pydantic import Field
import time


class AppointmentBase(BaseModel):
    tenant_id: str
    visitor_profile_id: Optional[str] = None
    host_id: str
    department_id: str
    # Branch this appointment belongs to. Resolved from the creating user's
    # token (or the tenant HQ for unscoped roles) by the route — never
    # client-supplied. Branch-null appointments predate branch separation
    # and are treated as HQ data. Drives the branchId list filter and the
    # branch_filter() read scoping for branch-scoped roles.
    branch_id: Optional[str] = None
    # Visitor identity captured at schedule time. Both are system-required
    # on create (see AppointmentCreate.validate_visitor_identity) unless an
    # existing visitor_profile_id is linked, in which case the profile
    # supplies them. Storing the phone here (not just the name) means the
    # appointment-driven check-in never has to prompt the receptionist for
    # a phone number that was already collected when the visit was booked.
    visitor_name_snapshot: Optional[str] = None
    visitor_phone: Optional[str] = None
    host_name_snapshot: Optional[str] = None
    scheduled_datetime: int
    purpose: Optional[str] = None
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    fulfilled_at: Optional[int] = None
    # Object key for a host-uploaded photo of the expected visitor.
    # Surfaced on the pending-approvals list so the receptionist can
    # match the person at the desk to the face the host pre-vetted.
    # Resolved to a presigned URL on read via
    # ``expected_visitor_photo_url`` on the *Out schema.
    expected_visitor_photo_object_key: Optional[str] = None
    # Tenant-configurable form data: free-form key/value dict whose
    # required keys come from the published TenantForm row with
    # ``target_type=appointment``. The service layer rejects creates
    # missing any field the form marks as ``required=True``. Snapshots
    # of the form_id / version that validated this row are stored so
    # historical submissions remain interpretable when the form is
    # later edited or archived.
    tenant_form_data: dict = Field(default_factory=dict)
    tenant_form_id: Optional[str] = None
    tenant_form_version: Optional[int] = None


class AppointmentCreate(AppointmentBase):
    # tenant_id is token-derived and injected by the route; never client-supplied.
    tenant_id: str = ""
    created_by: Optional[str] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_visitor_identity(self):
        """Visitor full name + phone are system-required at schedule time.

        These mirror ``SYSTEM_REQUIRED_APPOINTMENT_FIELDS`` and exist so the
        appointment-driven check-in (``check_in_from_appointment``) always has
        the visitor's name and phone on hand and never has to prompt the
        receptionist for them. The one exception is when an existing
        ``visitor_profile_id`` is linked — the stored profile supplies the
        identity, so the schedule form doesn't re-collect it.
        """
        if self.visitor_profile_id:
            return self
        missing = []
        if not (self.visitor_name_snapshot or "").strip():
            missing.append("visitor_name_snapshot")
        if not (self.visitor_phone or "").strip():
            missing.append("visitor_phone")
        if missing:
            raise ValueError(
                "Missing required visitor fields: "
                + ", ".join(missing)
                + " (required unless a visitor_profile_id is provided)"
            )
        return self


class AppointmentUpdate(BaseModel):
    visitor_profile_id: Optional[str] = None
    visitor_name_snapshot: Optional[str] = None
    visitor_phone: Optional[str] = None
    status: Optional[AppointmentStatus] = None
    scheduled_datetime: Optional[int] = None
    purpose: Optional[str] = None
    fulfilled_at: Optional[int] = None
    expected_visitor_photo_object_key: Optional[str] = None
    tenant_form_data: Optional[dict] = None
    tenant_form_id: Optional[str] = None
    tenant_form_version: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AppointmentOut(AppointmentBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_by: Optional[str] = None
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    # Presigned URL for the host-uploaded visitor photo. Resolved by the
    # service layer (best-effort — null if storage is misconfigured or
    # the object_key is unset).
    expected_visitor_photo_url: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


from schemas.summary_schema import (  # noqa: E402
    TenantBriefSummary,
    DepartmentBriefSummary,
    HostBriefSummary,
    UserBriefSummary,
    VisitorProfileBriefSummary,
    BranchBriefSummary,
)


class AppointmentWithSummaryOut(AppointmentOut):
    """AppointmentOut enriched with snapshots of every entity it references."""

    tenant_summary: Optional[TenantBriefSummary] = None
    department_summary: Optional[DepartmentBriefSummary] = None
    # host_summary resolves the host roster record (hosts collection).
    # For appointments created before the host rewire, host_id points at a
    # system_user and is mapped into the same HostBriefSummary shape.
    host_summary: Optional[HostBriefSummary] = None
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None
    created_by_summary: Optional[UserBriefSummary] = None
    branch_summary: Optional[BranchBriefSummary] = None
