from __future__ import annotations

import time
from schemas.imports import *


class CheckinPurpose(BaseModel):
    purpose: str
    purpose_details: Optional[str] = None
    expected_duration_minutes: Optional[int] = None


class CheckinBase(BaseModel):
    tenant_id: str
    visitor_id: str
    checkin_config_id: str
    id_extraction_id: Optional[str] = None
    tenant_specific_data: dict
    purpose: CheckinPurpose
    state: CheckinState = CheckinState.PENDING_APPROVAL
    verified: bool = False
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[int] = None
    rejection_reason: Optional[str] = None


class CheckinCreate(CheckinBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinUpdate(BaseModel):
    state: Optional[CheckinState] = None
    verified: Optional[bool] = None
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[int] = None
    rejection_reason: Optional[str] = None
    tenant_specific_data: Optional[dict] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinOut(CheckinBase):
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


class CheckinSubmitRequest(BaseModel):
    visitor_id: Optional[str] = None
    id_extraction_id: Optional[str] = None
    bio_data: dict
    tenant_specific_data: dict
    purpose: CheckinPurpose


class CheckinConfirmRequest(BaseModel):
    action: str  # "approve" or "reject"
    notes: Optional[str] = None


class CheckinListItem(BaseModel):
    visitor_name: str
    verified: bool
    purpose: str
    host_employee_id: Optional[str] = None
    created_at: int
