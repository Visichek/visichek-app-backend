from __future__ import annotations

from schemas.imports import *


# --- Enums ---


class SmtpEncryption(str, Enum):
    TLS = "tls"
    SSL = "ssl"
    NONE = "none"


# --- Platform Settings ---


class PlatformSettingsBase(BaseModel):
    """Global platform configuration managed by application admins."""

    # General
    platform_name: str = "VisiChek"
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    platform_url: Optional[str] = None

    # Security
    admin_enforce_totp: bool = False
    admin_session_timeout_minutes: int = 30
    admin_password_min_length: int = 12
    max_admin_accounts: Optional[int] = None

    # Tenant Defaults
    default_trial_days: int = 14
    default_plan_id: Optional[str] = None
    max_tenants_per_plan: dict[str, Optional[int]] = Field(default_factory=dict)

    # Email & SMTP
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_encryption: SmtpEncryption = SmtpEncryption.TLS

    # Feature Flags
    maintenance_mode: bool = False
    maintenance_message: Optional[str] = None
    signups_enabled: bool = True
    public_api_enabled: bool = True
    beta_features_enabled: bool = False

    # Rate Limiting Defaults
    global_rate_limit_per_minute: int = 80
    global_rate_limit_burst: int = 20


class PlatformSettingsCreate(PlatformSettingsBase):
    """Internal creation schema."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PlatformSettingsUpdate(BaseModel):
    """Partial update — all fields optional."""

    # General
    platform_name: Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    platform_url: Optional[str] = None

    # Security
    admin_enforce_totp: Optional[bool] = None
    admin_session_timeout_minutes: Optional[int] = None
    admin_password_min_length: Optional[int] = None
    max_admin_accounts: Optional[int] = None

    # Tenant Defaults
    default_trial_days: Optional[int] = None
    default_plan_id: Optional[str] = None
    max_tenants_per_plan: Optional[dict[str, Optional[int]]] = None

    # Email & SMTP
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_encryption: Optional[SmtpEncryption] = None

    # Feature Flags
    maintenance_mode: Optional[bool] = None
    maintenance_message: Optional[str] = None
    signups_enabled: Optional[bool] = None
    public_api_enabled: Optional[bool] = None
    beta_features_enabled: Optional[bool] = None

    # Rate Limiting Defaults
    global_rate_limit_per_minute: Optional[int] = None
    global_rate_limit_burst: Optional[int] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))


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
