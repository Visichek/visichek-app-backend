from __future__ import annotations

from schemas.imports import *
from pydantic import Field
from schemas.summary_schema import ContactBriefSummary
import time


class BranchStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class BranchBase(BaseModel):
    """A physical or logical location within a tenant's organisation."""

    tenant_id: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_headquarters: bool = False
    status: BranchStatus = BranchStatus.ACTIVE
    metadata: Optional[dict] = None
    # Designated point-of-contact: a system_user id in the SAME tenant. For
    # branch-scoped roles (dept_admin/receptionist/security_officer) the user
    # must have this branch in their branch_ids — validated in
    # services.branch_service.validate_branch_contact_user before writes.
    contact_user_id: Optional[str] = None


class BranchCreate(BranchBase):
    # tenant_id is NOT supplied by the client — the route always sets it from
    # the authenticated super_admin's token before the writer rebuilds this
    # model. Defaulted here so body validation doesn't reject a request that
    # (correctly) omits it.
    tenant_id: str = ""
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if not self.name or not self.name.strip():
            raise ValueError("Branch name is required")
        return self


class BranchUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_headquarters: Optional[bool] = None
    status: Optional[BranchStatus] = None
    metadata: Optional[dict] = None
    # None = "not provided" (route dumps with exclude_none, so None never
    # reaches the DB). Send "" to CLEAR a previously designated contact —
    # the writer normalises "" back to a stored None.
    contact_user_id: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class BranchOut(BranchBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    # Enriched best-effort in the service layer (never stored):
    # designated contact user -> branch email/phone -> main super admin.
    contact_summary: Optional[ContactBriefSummary] = None

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
