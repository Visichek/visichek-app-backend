from schemas.imports import *
from typing import Any, Dict, List
from pydantic import Field
import time


class PrivacyNoticeBase(BaseModel):
    tenant_id: str
    # Opaque, monotonic version identifier. Exposed publicly as ``versionId``.
    # Auto-minted by the service layer when omitted, and re-minted whenever the
    # legal text (title / summary / full_text / body / display_mode) changes so
    # consent records can prove WHICH text a visitor agreed to. Optional on the
    # wire — clients never set it.
    version_code: Optional[str] = None
    title: str
    summary: Optional[str] = None
    # The complete policy / terms text shown to the visitor (expandable). This
    # is the inline copy the kiosk renders; ``full_policy_url`` is the legacy
    # external-link field, retained for backward compatibility.
    full_text: Optional[str] = None
    # The rich, editor-authored copy as BlockNote content blocks. This is the
    # canonical source the tenant edits in the rich-text editor; ``full_text``
    # is kept as a flattened plain-text fallback for the kiosk consent gate.
    # Each block matches the BlockNote shape: {id, type, props, content,
    # children}. Empty for legacy notices authored before the migration.
    body: List[Dict[str, Any]] = Field(default_factory=list)
    full_policy_url: Optional[str] = None
    # Whether the visitor must explicitly accept (active_consent) or the notice
    # is merely displayed (passive). Defaults to active_consent for new notices.
    display_mode: NoticeDisplayMode = NoticeDisplayMode.ACTIVE_CONSENT
    effective_date: Optional[int] = None
    effective_from: Optional[int] = None
    effective_to: Optional[int] = None
    is_active: bool = True


class PrivacyNoticeCreate(PrivacyNoticeBase):
    # tenant_id is ALWAYS derived from the authenticated token by the route
    # layer and injected into the payload before the writer reconstructs this
    # model. Clients never supply it, so it defaults to "" on the request body
    # (a client-sent value is overwritten with the token's tenant_id).
    tenant_id: str = ""
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PrivacyNoticeUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: Optional[List[Dict[str, Any]]] = None
    full_policy_url: Optional[str] = None
    display_mode: Optional[NoticeDisplayMode] = None
    effective_date: Optional[int] = None
    effective_to: Optional[int] = None
    is_active: Optional[bool] = None
    # System-managed: set by the service layer when minting a new version.
    version_code: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PrivacyNoticeOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: str
    version_code: Optional[str] = None
    title: str
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: List[Dict[str, Any]] = Field(default_factory=list)
    full_policy_url: Optional[str] = None
    display_mode: NoticeDisplayMode = NoticeDisplayMode.ACTIVE_CONSENT
    is_active: bool = True
    effective_date: Optional[int] = None
    effective_from: Optional[int] = None
    effective_to: Optional[int] = None
    # Exposed on the wire as createdAt / updatedAt (the frontend contract).
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict):
            if "_id" in values and isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
            # Map legacy storage timestamps onto the wire names the FE expects.
            if (
                values.get("created_at") is None
                and values.get("date_created") is not None
            ):
                values["created_at"] = values["date_created"]
            if (
                values.get("updated_at") is None
                and values.get("last_updated") is not None
            ):
                values["updated_at"] = values["last_updated"]
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
