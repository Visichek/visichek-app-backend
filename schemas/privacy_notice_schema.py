from schemas.imports import *
from pydantic import Field
import time


class PrivacyNoticeBase(BaseModel):
    tenant_id: str
    version_code: str
    title: str
    summary: str
    full_policy_url: Optional[str] = None
    effective_from: Optional[int] = None
    effective_to: Optional[int] = None
    is_active: bool = True


class PrivacyNoticeCreate(PrivacyNoticeBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))


class PrivacyNoticeUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    full_policy_url: Optional[str] = None
    effective_to: Optional[int] = None
    is_active: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PrivacyNoticeOut(PrivacyNoticeBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None

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
