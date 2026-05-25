from schemas.imports import *
from pydantic import Field
import time


class PrivacyNoticeBase(BaseModel):
    tenant_id: str
    # Opaque, monotonic version identifier. Exposed publicly as ``versionId``.
    # Auto-minted by the service layer when omitted, and re-minted whenever the
    # legal text (title / summary / full_text / display_mode) changes so consent
    # records can prove WHICH text a visitor agreed to. Optional on the wire —
    # clients never set it.
    version_code: Optional[str] = None
    title: str
    summary: Optional[str] = None
    # The complete policy / terms text shown to the visitor (expandable). This
    # is the inline copy the kiosk renders; ``full_policy_url`` is the legacy
    # external-link field, retained for backward compatibility.
    full_text: Optional[str] = None
    full_policy_url: Optional[str] = None
    # Whether the visitor must explicitly accept (active_consent) or the notice
    # is merely displayed (passive). Defaults to active_consent for new notices.
    display_mode: NoticeDisplayMode = NoticeDisplayMode.ACTIVE_CONSENT
    effective_date: Optional[int] = None
    effective_from: Optional[int] = None
    effective_to: Optional[int] = None
    is_active: bool = True


class PrivacyNoticeCreate(PrivacyNoticeBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PrivacyNoticeUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
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
