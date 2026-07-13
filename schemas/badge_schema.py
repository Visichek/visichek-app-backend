from __future__ import annotations

import time
from schemas.imports import *


class BadgeBase(BaseModel):
    tenant_id: str
    checkin_id: str
    qr_code_value: str
    issued_at: int
    expires_at: int
    revoked_at: Optional[int] = None


class BadgeCreate(BadgeBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class BadgeOut(BadgeBase):
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


class BadgePayload(BaseModel):
    badge_id: str
    qr_code_value: str
    # Canonical name for the QR string across the rest of the API
    # (visit sessions, awaiting-checkout, appointment check-in all emit
    # ``badge_qr_token``). Kept in sync with ``qr_code_value`` by the
    # validator below so every badge-bearing response has one field name.
    badge_qr_token: str = ""
    visitor_name: str
    verified: bool
    portrait_url: Optional[str] = None
    host_employee_name: Optional[str] = None
    purpose: str
    issued_at: int
    expires_at: int

    @model_validator(mode="after")
    def mirror_qr_value(self) -> "BadgePayload":
        if not self.badge_qr_token:
            self.badge_qr_token = self.qr_code_value
        return self


class BadgeValidationResponse(BaseModel):
    valid: bool
    badge_id: Optional[str] = None
    qr_code_value: Optional[str] = None
    visitor_name: Optional[str] = None
    verified: Optional[bool] = None
    portrait_url: Optional[str] = None
    host_employee_name: Optional[str] = None
    purpose: Optional[str] = None
    issued_at: Optional[int] = None
    expires_at: Optional[int] = None
    reason: Optional[BadgeValidationReason] = None
