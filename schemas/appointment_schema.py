from schemas.imports import *
from pydantic import Field
import time


class AppointmentBase(BaseModel):
    tenant_id: str
    visitor_profile_id: Optional[str] = None
    host_id: str
    department_id: str
    visitor_name_snapshot: Optional[str] = None
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
    created_by: Optional[str] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AppointmentUpdate(BaseModel):
    visitor_profile_id: Optional[str] = None
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
    UserBriefSummary,
    VisitorProfileBriefSummary,
)


class AppointmentWithSummaryOut(AppointmentOut):
    """AppointmentOut enriched with snapshots of every entity it references."""

    tenant_summary: Optional[TenantBriefSummary] = None
    department_summary: Optional[DepartmentBriefSummary] = None
    host_summary: Optional[UserBriefSummary] = None
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None
    created_by_summary: Optional[UserBriefSummary] = None
