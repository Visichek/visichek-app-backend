from __future__ import annotations

from schemas.imports import *


# --- Enums ---

class VisitorBadgeExpiry(str, Enum):
    END_OF_DAY = "end_of_day"
    MANUAL = "manual"
    HOURS = "hours"


class SsoProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    OKTA = "okta"
    CUSTOM = "custom"


# --- Tenant Settings ---

class TenantSettingsBase(BaseModel):
    """Organization-level configuration managed by super_admin."""

    tenant_id: str

    # General
    company_name: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_address: Optional[str] = None
    default_timezone: str = "Africa/Lagos"
    default_language: str = "en"

    # Security Policies
    enforce_totp: bool = False
    password_min_length: int = 8
    password_require_uppercase: bool = True
    password_require_number: bool = True
    password_require_special_char: bool = True
    password_expiry_days: Optional[int] = None
    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 30
    session_timeout_minutes: int = 60
    allowed_ip_ranges: Optional[List[str]] = None

    # Visitor Policies
    require_id_scan: bool = False
    require_host_approval: bool = False
    require_consent_before_check_in: bool = True
    auto_checkout_after_hours: Optional[int] = None
    visitor_badge_expiry: VisitorBadgeExpiry = VisitorBadgeExpiry.END_OF_DAY
    visitor_badge_expiry_hours: Optional[int] = None
    allow_self_registration: bool = False
    self_registration_fields: List[str] = Field(
        default_factory=lambda: ["company", "purpose", "email"]
    )

    # Data Retention
    visitor_data_retention_days: int = 365
    audit_log_retention_days: int = 730
    incident_retention_days: int = 730
    deletion_action: DeletionAction = DeletionAction.ANONYMISE

    # Notifications
    send_welcome_email: bool = True
    send_visitor_badge_email: bool = False
    send_host_notification_on_arrival: bool = True
    incident_escalation_email: Optional[str] = None

    # Integrations
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    webhook_events: List[str] = Field(default_factory=list)
    sso_enabled: bool = False
    sso_provider: Optional[SsoProvider] = None

    @model_validator(mode="after")
    def validate_settings(self):
        if self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8")
        if self.password_min_length > 128:
            raise ValueError("password_min_length must be at most 128")
        if self.visitor_badge_expiry == VisitorBadgeExpiry.HOURS and not self.visitor_badge_expiry_hours:
            raise ValueError("visitor_badge_expiry_hours is required when badge expiry is set to 'hours'")
        return self


class TenantSettingsCreate(TenantSettingsBase):
    """Internal creation schema."""
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantSettingsUpdate(BaseModel):
    """Partial update — all fields optional."""

    # General
    company_name: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_address: Optional[str] = None
    default_timezone: Optional[str] = None
    default_language: Optional[str] = None

    # Security Policies
    enforce_totp: Optional[bool] = None
    password_min_length: Optional[int] = None
    password_require_uppercase: Optional[bool] = None
    password_require_number: Optional[bool] = None
    password_require_special_char: Optional[bool] = None
    password_expiry_days: Optional[int] = None
    max_failed_login_attempts: Optional[int] = None
    lockout_duration_minutes: Optional[int] = None
    session_timeout_minutes: Optional[int] = None
    allowed_ip_ranges: Optional[List[str]] = None

    # Visitor Policies
    require_id_scan: Optional[bool] = None
    require_host_approval: Optional[bool] = None
    require_consent_before_check_in: Optional[bool] = None
    auto_checkout_after_hours: Optional[int] = None
    visitor_badge_expiry: Optional[VisitorBadgeExpiry] = None
    visitor_badge_expiry_hours: Optional[int] = None
    allow_self_registration: Optional[bool] = None
    self_registration_fields: Optional[List[str]] = None

    # Data Retention
    visitor_data_retention_days: Optional[int] = None
    audit_log_retention_days: Optional[int] = None
    incident_retention_days: Optional[int] = None
    deletion_action: Optional[DeletionAction] = None

    # Notifications
    send_welcome_email: Optional[bool] = None
    send_visitor_badge_email: Optional[bool] = None
    send_host_notification_on_arrival: Optional[bool] = None
    incident_escalation_email: Optional[str] = None

    # Integrations
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    webhook_events: Optional[List[str]] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_settings(self):
        if self.password_min_length is not None:
            if self.password_min_length < 8:
                raise ValueError("password_min_length must be at least 8")
            if self.password_min_length > 128:
                raise ValueError("password_min_length must be at most 128")
        return self


class TenantSettingsOut(TenantSettingsBase):
    """Response schema for tenant settings."""
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
