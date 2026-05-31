from schemas.imports import *
from pydantic import Field
import time

from schemas.summary_schema import VisitorProfileBriefSummary


class DSRBase(BaseModel):
    tenant_id: str
    visitor_profile_id: str
    admin_id: Optional[str] = None
    visit_session_id: Optional[str] = None
    request_type: DSRType
    status: DSRStatus = DSRStatus.PENDING
    identity_verified: bool = False
    sla_deadline: Optional[int] = None
    notes: Optional[str] = None


class DSRCreate(DSRBase):
    # tenant_id is token-derived and injected by the route; never client-supplied.
    tenant_id: str = ""
    received_at: int = Field(default_factory=lambda: int(time.time()))
    date_created: int = Field(default_factory=lambda: int(time.time()))


class DSRUpdate(BaseModel):
    admin_id: Optional[str] = None
    status: Optional[DSRStatus] = None
    identity_verified: Optional[bool] = None
    notes: Optional[str] = None
    # Documented outcome of a terminal transition. The complete/reject
    # endpoints (and bulk reject) send these; without declaring them here
    # they were silently dropped and never persisted.
    resolution: Optional[str] = None
    rejection_reason: Optional[str] = None
    resolved_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DSROut(DSRBase):
    id: Optional[str] = Field(default=None, alias="_id")
    received_at: Optional[int] = None
    resolved_at: Optional[int] = None
    resolution: Optional[str] = None
    rejection_reason: Optional[str] = None
    date_created: Optional[int] = None
    # Embedded snapshot of the subject this request concerns, so the DPO UI
    # can show a name next to visitor_profile_id without a follow-up fetch
    # (External ID Summary Fields rule). Resolved in the service layer.
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None

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
