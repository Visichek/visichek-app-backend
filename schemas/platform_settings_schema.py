from __future__ import annotations

from schemas.imports import *


# --- Platform Settings (runtime-editable subset) ---
#
# Everything that used to live here — password policy, account lockout,
# session timeout, 2FA enforcement, SMTP, tenant defaults, rate limits, and
# the non-maintenance feature flags — now lives in code at
# ``config/platform_config.py`` and is changed by a deploy, not the API.
#
# The ONLY runtime-editable platform setting is maintenance mode, and
# toggling it requires an OTP challenge (see services/platform_settings_service.py).


class PlatformSettingsBase(BaseModel):
    """Runtime-editable platform configuration (maintenance only)."""

    maintenance_mode: bool = False
    maintenance_message: Optional[str] = None


class PlatformSettingsCreate(PlatformSettingsBase):
    """Internal creation schema for the singleton."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PlatformSettingsUpdate(BaseModel):
    """Internal partial-update schema (used by the repository layer)."""

    maintenance_mode: Optional[bool] = None
    maintenance_message: Optional[str] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))


class MaintenanceModeUpdateRequest(BaseModel):
    """OTP-gated request to change maintenance mode.

    Step 1 mints an OTP challenge; the frontend prompts for the code and
    submits it here with the desired maintenance state.
    """

    otp_challenge_id: str
    otp_code: str
    maintenance_mode: bool
    maintenance_message: Optional[str] = None


class PlatformSettingsOut(PlatformSettingsBase):
    """Response schema for platform settings."""

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
