from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class HostBase(BaseModel):
    """A person a visitor can be scheduled to see.

    A host is either backed by an existing tenant ``system_user``
    (``source_system_user_id`` set) or a *dedicated* host that exists only
    as a host record (``source_system_user_id`` is ``None``). Either way the
    contact fields (name / phone / email / picture / signature) are stored as
    a snapshot on the host record so reads never need to follow the link.
    """

    tenant_id: str
    name: str
    phone: str
    email: Optional[str] = None
    department_id: str
    picture_image_url: Optional[str] = None
    signature_image_url: Optional[str] = None
    # Set when the host mirrors a tenant system user; None for dedicated hosts.
    source_system_user_id: Optional[str] = None
    is_active: bool = True


class HostCreate(BaseModel):
    # tenant_id is NOT supplied by the client — the route sets it from the
    # authenticated principal's token before the writer rebuilds this model.
    # Defaulted so body validation doesn't reject a request that (correctly)
    # omits it.
    tenant_id: str = ""
    name: str
    phone: str
    email: Optional[str] = None
    department_id: str
    picture_image_url: Optional[str] = None
    signature_image_url: Optional[str] = None
    source_system_user_id: Optional[str] = None
    is_active: bool = True
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if not self.name or not self.name.strip():
            raise ValueError("Host name is required")
        if not self.phone or not self.phone.strip():
            raise ValueError("Host phone number is required")
        if not self.department_id or not self.department_id.strip():
            raise ValueError("Host department is required")
        return self


class HostUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    department_id: Optional[str] = None
    picture_image_url: Optional[str] = None
    signature_image_url: Optional[str] = None
    is_active: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class HostOut(HostBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


from schemas.summary_schema import (  # noqa: E402
    DepartmentBriefSummary,
    TenantBriefSummary,
    UserBriefSummary,
)


class HostWithSummaryOut(HostOut):
    """HostOut enriched with snapshots of the entities its IDs reference."""

    tenant_summary: Optional[TenantBriefSummary] = None
    department_summary: Optional[DepartmentBriefSummary] = None
    source_system_user_summary: Optional[UserBriefSummary] = None
