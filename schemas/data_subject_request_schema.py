from schemas.imports import *
from pydantic import Field
import time


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
    received_at: int = Field(default_factory=lambda: int(time.time()))
    date_created: int = Field(default_factory=lambda: int(time.time()))


class DSRUpdate(BaseModel):
    admin_id: Optional[str] = None
    status: Optional[DSRStatus] = None
    identity_verified: Optional[bool] = None
    notes: Optional[str] = None
    resolved_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DSROut(DSRBase):
    id: Optional[str] = Field(default=None, alias="_id")
    received_at: Optional[int] = None
    resolved_at: Optional[int] = None
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
