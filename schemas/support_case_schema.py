from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


# Priority-to-SLA mapping used by the service layer.
SLA_WINDOWS_SECONDS: dict[str, int] = {
    SupportCasePriority.CRITICAL.value: 4 * 3600,
    SupportCasePriority.HIGH.value: 24 * 3600,
    SupportCasePriority.MEDIUM.value: 72 * 3600,
    SupportCasePriority.LOW.value: 7 * 24 * 3600,
}

# Statuses that count as "open" against the tenant's 10-open-case cap.
OPEN_STATUSES: tuple[str, ...] = (
    SupportCaseStatus.OPEN.value,
    SupportCaseStatus.ACKNOWLEDGED.value,
    SupportCaseStatus.IN_PROGRESS.value,
    SupportCaseStatus.AWAITING_TENANT.value,
    SupportCaseStatus.RESOLVED.value,
    SupportCaseStatus.REOPENED.value,
)


# --- Attachment ---


class SupportCaseAttachment(BaseModel):
    """Reference to a document uploaded for a case message."""

    document_id: str
    file_name: str
    mime_type: Optional[str] = None
    size: Optional[int] = None
    object_key: Optional[str] = None


# --- Support Case (metadata) ---


class SupportCaseBase(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=10_000)
    category: SupportCaseCategory = SupportCaseCategory.OTHER
    priority: SupportCasePriority = SupportCasePriority.MEDIUM


class SupportCaseCreate(SupportCaseBase):
    tenant_id: str
    opened_by: str
    opened_by_role: str
    status: SupportCaseStatus = SupportCaseStatus.OPEN
    assigned_admin_id: Optional[str] = None
    last_message_at: Optional[int] = None
    message_count: int = 0
    attachment_count: int = 0
    sla_due_at: Optional[int] = None
    resolved_at: Optional[int] = None
    closed_at: Optional[int] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def _compute_sla(self):
        if self.sla_due_at is None:
            window = SLA_WINDOWS_SECONDS.get(
                self.priority.value,
                SLA_WINDOWS_SECONDS[SupportCasePriority.MEDIUM.value],
            )
            self.sla_due_at = self.date_created + window
        return self


class SupportCaseUpdate(BaseModel):
    status: Optional[SupportCaseStatus] = None
    priority: Optional[SupportCasePriority] = None
    assigned_admin_id: Optional[str] = None
    last_message_at: Optional[int] = None
    message_count: Optional[int] = None
    attachment_count: Optional[int] = None
    sla_due_at: Optional[int] = None
    resolved_at: Optional[int] = None
    closed_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class SupportCaseOut(SupportCaseBase):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: Optional[str] = None
    opened_by: Optional[str] = None
    opened_by_role: Optional[str] = None
    status: Optional[SupportCaseStatus] = None
    assigned_admin_id: Optional[str] = None
    last_message_at: Optional[int] = None
    message_count: int = 0
    attachment_count: int = 0
    sla_due_at: Optional[int] = None
    resolved_at: Optional[int] = None
    closed_at: Optional[int] = None
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


# --- Support Case Message (thread entry) ---


class SupportCaseMessageBase(BaseModel):
    body: str = Field(..., min_length=1, max_length=20_000)
    internal_note: bool = False
    attachments: List[SupportCaseAttachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cap_attachments(self):
        if len(self.attachments) > 10:
            raise ValueError("A message can carry at most 10 attachments")
        return self


class SupportCaseMessageCreate(SupportCaseMessageBase):
    case_id: str
    author_id: str
    author_role: str
    author_type: SupportCaseAuthorType
    date_created: int = Field(default_factory=lambda: int(time.time()))


class SupportCaseMessageOut(SupportCaseMessageBase):
    id: Optional[str] = Field(default=None, alias="_id")
    case_id: Optional[str] = None
    author_id: Optional[str] = None
    author_role: Optional[str] = None
    author_type: Optional[SupportCaseAuthorType] = None
    date_created: Optional[int] = None

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


# --- Request bodies for routes ---


class SupportCaseOpenRequest(SupportCaseBase):
    """Public-facing body for POST /v1/support-cases."""

    pass


class SupportCaseMessageRequest(BaseModel):
    """Public-facing body for POST /v1/support-cases/{id}/messages."""

    body: str = Field(..., min_length=1, max_length=20_000)
    attachments: List[SupportCaseAttachment] = Field(default_factory=list)
    # Admin callers may set this; ignored (forced False) when actor is a tenant.
    internal_note: bool = False


class SupportCaseTransitionRequest(BaseModel):
    status: SupportCaseStatus


class SupportCaseAssignRequest(BaseModel):
    admin_id: str


class SupportCaseAttachmentIntentRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=256)
    mime_type: Optional[str] = None
    size: Optional[int] = Field(default=None, ge=0)


class SupportCaseAttachmentIntentResponse(BaseModel):
    upload_url: str
    object_key: str
    method: str = "PUT"
    headers: dict = Field(default_factory=dict)
    expires_in: int = 900


from schemas.summary_schema import TenantBriefSummary, UserBriefSummary  # noqa: E402


class SupportCaseWithSummaryOut(SupportCaseOut):
    """SupportCaseOut enriched with snapshots of every foreign-key it carries.

    Each summary field mirrors the ID field alongside it so the frontend
    can render a label/badge without a second round-trip.
    """

    tenant_summary: Optional[TenantBriefSummary] = None
    opened_by_summary: Optional[UserBriefSummary] = None
    assigned_admin_summary: Optional[UserBriefSummary] = None


class SupportCaseMessageWithSummaryOut(SupportCaseMessageOut):
    """SupportCaseMessageOut enriched with an author snapshot."""

    author_summary: Optional[UserBriefSummary] = None
