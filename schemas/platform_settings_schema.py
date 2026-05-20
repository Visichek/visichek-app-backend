from __future__ import annotations

from schemas.imports import *


# --- Enums ---


class SmtpEncryption(str, Enum):
    TLS = "tls"
    SSL = "ssl"
    NONE = "none"


# --- Platform Settings ---


class PlatformSettingsBase(BaseModel):
    """Global platform configuration managed by application admins.

    Security policies (password rules, account lockout, session timeout,
    2FA enforcement) live here and apply uniformly to every account on
    the platform — admins, tenant super_admins, and all tenant users.
    Tenants cannot override these.
    """

    # General
    platform_name: str = "VisiChek"
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    platform_url: Optional[str] = None

    # Password Policy (platform-wide)
    password_min_length: int = 8
    password_max_length: int = 128
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_number: bool = True
    password_require_special_char: bool = True
    password_expiry_days: Optional[int] = None
    password_history_count: int = 5

    # Account Lockout (platform-wide)
    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 15

    # Session (platform-wide)
    session_timeout_minutes: int = 60

    # Two-Factor Authentication (platform-wide)
    enforce_totp_for_admins: bool = True
    enforce_totp_for_tenant_users: bool = False

    # Admin Account Limits
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
    # Self-Onboarding (public marketing-site form). When False the public
    # POST /v1/onboarding/submissions endpoint returns 403 self_onboarding_disabled.
    # The endpoint itself remains mounted regardless.
    self_onboarding_enabled: bool = True

    # Rate Limiting Defaults
    global_rate_limit_per_minute: int = 80
    global_rate_limit_burst: int = 20

    @model_validator(mode="after")
    def validate_security_settings(self):
        if self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8")
        if self.password_min_length > self.password_max_length:
            raise ValueError("password_min_length cannot exceed password_max_length")
        if self.password_max_length > 256:
            raise ValueError("password_max_length must be at most 256")
        if self.password_history_count < 0 or self.password_history_count > 24:
            raise ValueError("password_history_count must be between 0 and 24")
        if self.max_failed_login_attempts < 1:
            raise ValueError("max_failed_login_attempts must be at least 1")
        if self.lockout_duration_minutes < 1:
            raise ValueError("lockout_duration_minutes must be at least 1")
        if self.session_timeout_minutes < 5:
            raise ValueError("session_timeout_minutes must be at least 5")
        if self.password_expiry_days is not None and self.password_expiry_days < 1:
            raise ValueError("password_expiry_days must be at least 1 if set")
        return self


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

    # Password Policy
    password_min_length: Optional[int] = None
    password_max_length: Optional[int] = None
    password_require_uppercase: Optional[bool] = None
    password_require_lowercase: Optional[bool] = None
    password_require_number: Optional[bool] = None
    password_require_special_char: Optional[bool] = None
    password_expiry_days: Optional[int] = None
    password_history_count: Optional[int] = None

    # Account Lockout
    max_failed_login_attempts: Optional[int] = None
    lockout_duration_minutes: Optional[int] = None

    # Session
    session_timeout_minutes: Optional[int] = None

    # Two-Factor Authentication
    enforce_totp_for_admins: Optional[bool] = None
    enforce_totp_for_tenant_users: Optional[bool] = None

    # Admin Account Limits
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
    self_onboarding_enabled: Optional[bool] = None

    # Rate Limiting Defaults
    global_rate_limit_per_minute: Optional[int] = None
    global_rate_limit_burst: Optional[int] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_security_updates(self):
        if self.password_min_length is not None and self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8")
        if self.password_max_length is not None and self.password_max_length > 256:
            raise ValueError("password_max_length must be at most 256")
        if (
            self.password_min_length is not None
            and self.password_max_length is not None
            and self.password_min_length > self.password_max_length
        ):
            raise ValueError("password_min_length cannot exceed password_max_length")
        if self.password_history_count is not None and (
            self.password_history_count < 0 or self.password_history_count > 24
        ):
            raise ValueError("password_history_count must be between 0 and 24")
        if (
            self.max_failed_login_attempts is not None
            and self.max_failed_login_attempts < 1
        ):
            raise ValueError("max_failed_login_attempts must be at least 1")
        if (
            self.lockout_duration_minutes is not None
            and self.lockout_duration_minutes < 1
        ):
            raise ValueError("lockout_duration_minutes must be at least 1")
        if (
            self.session_timeout_minutes is not None
            and self.session_timeout_minutes < 5
        ):
            raise ValueError("session_timeout_minutes must be at least 5")
        if self.password_expiry_days is not None and self.password_expiry_days < 1:
            raise ValueError("password_expiry_days must be at least 1 if set")
        return self


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
