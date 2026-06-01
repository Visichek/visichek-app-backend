from schemas.imports import *
from pydantic import Field
import time

from schemas.summary_schema import (
    UserBriefSummary,
    VisitorProfileBriefSummary,
    VisitSessionBriefSummary,
)


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
    # Right-of-access fulfilment artifact. Stamped by the dsr.fulfil_access
    # writer once the export ZIP is generated, stored, and emailed so the DSR
    # carries a durable record of what was sent and where.
    access_export_object_key: Optional[str] = None
    access_export_expires_at: Optional[int] = None
    access_export_emailed_to: Optional[str] = None
    access_export_generated_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DSROut(DSRBase):
    id: Optional[str] = Field(default=None, alias="_id")
    received_at: Optional[int] = None
    resolved_at: Optional[int] = None
    resolution: Optional[str] = None
    rejection_reason: Optional[str] = None
    date_created: Optional[int] = None
    # Embedded snapshots so the DPO UI can render a name/status next to each
    # foreign-key id without a follow-up fetch (External ID Summary Fields
    # rule). Every *_id on this *Out carries a *_summary companion. Resolved
    # concurrently in the service layer (see _enrich_dsr).
    visitor_profile_summary: Optional[VisitorProfileBriefSummary] = None
    # admin_id is stamped from the DPO/super_admin actor (a system_user), so
    # it resolves via resolve_user_summary (system_user-first), not the
    # application-admin resolver.
    admin_summary: Optional[UserBriefSummary] = None
    visit_session_summary: Optional[VisitSessionBriefSummary] = None
    # Right-of-access fulfilment artifact (see DSRUpdate).
    access_export_object_key: Optional[str] = None
    access_export_expires_at: Optional[int] = None
    access_export_emailed_to: Optional[str] = None
    access_export_generated_at: Optional[int] = None

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


class DSRCorrectionRequest(BaseModel):
    """Body for fulfilling a *correction* DSR.

    Allowlists exactly the visitor-profile PII a data subject can have
    corrected. Every field is optional; only the supplied (non-None) fields
    are applied to the visitor profile.
    """

    full_name: Optional[str] = None
    phone: Optional[str] = None
    email_address: Optional[EmailStr] = None
    company: Optional[str] = None
