from __future__ import annotations

from schemas.imports import *


# --- Enums ---


class DeviceType(str, Enum):
    DESKTOP = "desktop"
    TABLET = "tablet"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


# --- Session ---


class SessionBase(BaseModel):
    """An active user session."""

    user_id: str
    user_type: str  # "admin" or "system_user"
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_type: DeviceType = DeviceType.UNKNOWN
    device: Optional[str] = None  # Human-readable label e.g. "Chrome (Windows)"
    location: Optional[str] = None
    is_current: bool = False


class SessionCreate(SessionBase):
    """Internal creation schema."""

    access_token_id: str
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_active_at: int = Field(default_factory=lambda: int(time.time()))


class SessionOut(SessionBase):
    """Response schema for a session."""

    id: Optional[str] = Field(default=None, alias="_id")
    access_token_id: Optional[str] = None
    date_created: Optional[int] = None
    last_active_at: Optional[int] = None

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


# --- Password Change ---


class ChangePasswordRequest(BaseModel):
    """Request body for changing a password."""

    current_password: str
    new_password: str


# --- Two-Factor Authentication ---


class TwoFactorSetupOut(BaseModel):
    """Returned when initiating 2FA setup."""

    secret: str
    qr_code_uri: str
    backup_codes: List[str]


class TwoFactorVerifyRequest(BaseModel):
    """Verify a TOTP code or backup code."""

    code: str


class TwoFactorAuthenticateRequest(BaseModel):
    """Step 2 of 2FA login — verify code with temp token."""

    temp_token: str
    code: str


class TwoFactorDisableRequest(BaseModel):
    """Disable 2FA — requires password for confirmation."""

    password: str


class BackupCodesRegenerateRequest(BaseModel):
    """Regenerate backup codes — requires password for confirmation."""

    password: str


class AccountDeleteRequest(BaseModel):
    """Request account deletion — requires password confirmation."""

    password: str
