from __future__ import annotations

import time
from typing import Any

from schemas.imports import *


class KYCVerificationBase(BaseModel):
    """Per-checkin record of a KYC attempt.

    One row per check-in submission that triggers KYC. Keyed by
    ``reference_id`` once the provider mints one (the visichek
    correlator equals the check-in id until then).
    """

    tenant_id: str
    checkin_id: str
    visitor_id: Optional[str] = None
    provider: str = "dojah"
    reference_id: Optional[str] = None
    status: KYCStatus = KYCStatus.PENDING
    confidence: Optional[float] = None
    extracted_full_name: Optional[str] = None
    extracted_dob: Optional[str] = None
    extracted_id_number: Optional[str] = None
    extracted_id_type: Optional[str] = None
    selfie_url: Optional[str] = None
    id_image_url: Optional[str] = None
    failure_reason: Optional[str] = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class KYCVerificationCreate(KYCVerificationBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class KYCVerificationUpdate(BaseModel):
    reference_id: Optional[str] = None
    status: Optional[KYCStatus] = None
    confidence: Optional[float] = None
    extracted_full_name: Optional[str] = None
    extracted_dob: Optional[str] = None
    extracted_id_number: Optional[str] = None
    extracted_id_type: Optional[str] = None
    selfie_url: Optional[str] = None
    id_image_url: Optional[str] = None
    failure_reason: Optional[str] = None
    raw_payload: Optional[dict[str, Any]] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class KYCVerificationOut(KYCVerificationBase):
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


class KYCInitiateRequestIn(BaseModel):
    """Public kiosk payload for ``POST /v1/kyc/initiate``.

    Visitor and tenant identity are taken from the supplied
    ``checkin_id`` so the kiosk only has to send the correlator. The
    backend looks up the check-in, asserts state, and builds the
    widget config the frontend launches Dojah with.
    """

    checkin_id: str


class KYCInitiateResponseOut(BaseModel):
    reference_id: str
    provider: str
    widget_config: dict[str, Any]
    expires_at: Optional[int] = None


class KYCSkipRequestIn(BaseModel):
    """Visitor opted not to verify with Dojah.

    Allowed only when ``kyc_required`` is ``False`` on the tenant
    settings; otherwise the route returns 403.
    """

    checkin_id: str
    reason: Optional[str] = None


class KYCStatusOut(BaseModel):
    """Polling fallback for the kiosk if the webhook is slow.

    Returns the visichek-side status (which the webhook keeps in sync
    with the provider) so the kiosk doesn't have to know Dojah's
    state machine.
    """

    checkin_id: str
    reference_id: Optional[str] = None
    status: KYCStatus
    failure_reason: Optional[str] = None
