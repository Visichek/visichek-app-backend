from __future__ import annotations

import time
from pydantic import ConfigDict
from schemas.imports import *
from schemas.summary_schema import ManualVerificationInfo


class VisitorBase(BaseModel):
    tenant_id: str
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    bio_data: dict = Field(default_factory=dict)
    verified: bool = False
    verification_method: Optional[IDType] = None
    id_number_encrypted: Optional[str] = None
    id_document_id: Optional[str] = None
    portrait_url: Optional[str] = None
    # Staff-vouched verification attribution. Set only via
    # POST /v1/checkins/{checkin_id}/manual-verify; null otherwise.
    manual_verification: Optional[ManualVerificationInfo] = None


class VisitorCreate(VisitorBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    bio_data: Optional[dict] = None
    verified: Optional[bool] = None
    verification_method: Optional[IDType] = None
    id_number_encrypted: Optional[str] = None
    id_document_id: Optional[str] = None
    portrait_url: Optional[str] = None
    manual_verification: Optional[ManualVerificationInfo] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorEditRequest(BaseModel):
    """Staff edit of a visitor's identity fields (PATCH /v1/visitors/{id}).

    Partial update: only keys PRESENT in the body are touched. For the
    nullable fields (``email`` / ``company``) an explicit ``null`` clears
    the value; an absent key leaves it unchanged. Identity / permission
    fields (``verified``, ``verification_method``, role, tenant_id) are
    intentionally NOT accepted here — extras are ignored."""

    model_config = ConfigDict(extra="ignore")

    # ``email`` is a plain str here (not EmailStr) so the service can emit a
    # field-mapped 422 (``details.email``) instead of FastAPI's default
    # request-validation error shape. Format is validated in the service.
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None


class VisitorOut(VisitorBase):
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


class VisitorLookupResponse(BaseModel):
    found: bool
    visitor: Optional[VisitorOut] = None
