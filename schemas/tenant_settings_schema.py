from __future__ import annotations

from pydantic import ConfigDict

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


class KYCProviderName(str, Enum):
    """Available KYC providers. Plan tier gates whether the tenant can
    actually use any of these — see ``services.kyc_service.kyc_available``.
    """

    DOJAH = "dojah"


# --- Tenant Settings ---


class TenantSettingsBase(BaseModel):
    """Organization-level configuration managed by super_admin.

    Security policies (password rules, account lockout, session timeout,
    2FA enforcement, IP allow-list) are NOT configured here — they are
    owned by the platform admin via ``PlatformSettings`` and applied
    uniformly to every tenant. Only operational policies that are
    safe for a tenant to control live on this schema.
    """

    tenant_id: str

    # General
    company_name: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_address: Optional[str] = None
    default_timezone: str = "Africa/Lagos"
    default_language: str = "en"

    # Visitor Policies
    require_id_scan: bool = False
    require_consent_before_check_in: bool = True
    # Visitors still shown as on-site after this many hours are closed by
    # the auto-checkout sweep (services/auto_checkout_service.py, every
    # 15 min). None or 0 disables the sweep for the tenant. Existing
    # tenants are backfilled to 12 once via backfill_auto_checkout_default.
    auto_checkout_after_hours: Optional[int] = 12
    visitor_badge_expiry: VisitorBadgeExpiry = VisitorBadgeExpiry.END_OF_DAY
    visitor_badge_expiry_hours: Optional[int] = None
    allow_self_registration: bool = False
    self_registration_fields: List[str] = Field(
        default_factory=lambda: ["company", "purpose", "email"]
    )
    # Skip ID re-verification if the visitor's last_verification_date falls
    # within this window. 0 disables the shortcut (every visit re-verifies).
    id_reverification_days: int = 30

    # Geofencing — see backend-docs/geofencing.md.
    # When enabled, visitor submits must include lat/lng and be within
    # ``geofencing_radius_meters`` of either the tenant's fixed
    # ``geofencing_reference_lat/lng`` (if configured) or any approver who
    # has reported a location in the last 10 minutes.
    geofencing_enabled: bool = False
    geofencing_radius_meters: int = 50
    geofencing_reference_lat: Optional[float] = None
    geofencing_reference_lng: Optional[float] = None

    # KYC (identity verification at kiosk submit). Availability is
    # plan-gated — see ``services.kyc_service.kyc_available_for_tenant``.
    # Even when the plan grants access, ``kyc_required`` decides whether
    # visitors can skip the widget. ``kyc_provider`` is the provider key
    # passed to ``KYCManager.get_provider`` (currently only "dojah").
    # ``kyc_methods`` is the ordered list of Dojah verification types
    # the kiosk should request — empty means "use the provider default".
    kyc_required: bool = False
    kyc_provider: KYCProviderName = KYCProviderName.DOJAH
    kyc_methods: List[str] = Field(default_factory=list)

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

    # Beta features — org-wide opt-in to early-access UI in the tenant app
    # (chat-style support cases, incident calendar). Toggled by the
    # super_admin from Settings → Advanced; surfaced to every tenant role
    # through the /system-users/me tenant summary.
    beta_features_enabled: bool = False

    @model_validator(mode="after")
    def validate_settings(self):
        if (
            self.visitor_badge_expiry == VisitorBadgeExpiry.HOURS
            and not self.visitor_badge_expiry_hours
        ):
            raise ValueError(
                "visitor_badge_expiry_hours is required when badge expiry is set to 'hours'"
            )
        if self.geofencing_radius_meters < 5 or self.geofencing_radius_meters > 5000:
            raise ValueError("geofencing_radius_meters must be between 5 and 5000")
        has_lat = self.geofencing_reference_lat is not None
        has_lng = self.geofencing_reference_lng is not None
        if has_lat != has_lng:
            raise ValueError(
                "geofencing_reference_lat and geofencing_reference_lng must be set together"
            )
        if has_lat and not (-90 <= (self.geofencing_reference_lat or 0) <= 90):
            raise ValueError("geofencing_reference_lat must be between -90 and 90")
        if has_lng and not (-180 <= (self.geofencing_reference_lng or 0) <= 180):
            raise ValueError("geofencing_reference_lng must be between -180 and 180")
        return self


class TenantSettingsCreate(TenantSettingsBase):
    """Internal creation schema."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantSettingsUpdate(BaseModel):
    """Partial update — all fields optional.

    Any payload key not declared here is silently ignored by Pydantic, so
    leftover security-policy fields the frontend may still be sending
    (``password_min_length``, ``session_timeout_minutes``,
    ``max_failed_login_attempts`` etc.) are dropped at the boundary
    rather than persisted.
    """

    # General
    company_name: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_address: Optional[str] = None
    default_timezone: Optional[str] = None
    default_language: Optional[str] = None

    # Visitor Policies
    require_id_scan: Optional[bool] = None
    require_consent_before_check_in: Optional[bool] = None
    auto_checkout_after_hours: Optional[int] = None
    visitor_badge_expiry: Optional[VisitorBadgeExpiry] = None
    visitor_badge_expiry_hours: Optional[int] = None
    allow_self_registration: Optional[bool] = None
    self_registration_fields: Optional[List[str]] = None
    id_reverification_days: Optional[int] = None
    geofencing_enabled: Optional[bool] = None
    geofencing_radius_meters: Optional[int] = None
    geofencing_reference_lat: Optional[float] = None
    geofencing_reference_lng: Optional[float] = None

    # KYC (see TenantSettingsBase docs).
    kyc_required: Optional[bool] = None
    kyc_provider: Optional[KYCProviderName] = None
    kyc_methods: Optional[List[str]] = None

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

    # Beta features
    beta_features_enabled: Optional[bool] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def validate_settings(self):
        if self.geofencing_radius_meters is not None:
            if (
                self.geofencing_radius_meters < 5
                or self.geofencing_radius_meters > 5000
            ):
                raise ValueError("geofencing_radius_meters must be between 5 and 5000")
        has_lat = self.geofencing_reference_lat is not None
        has_lng = self.geofencing_reference_lng is not None
        if has_lat != has_lng:
            raise ValueError(
                "geofencing_reference_lat and geofencing_reference_lng must be set together"
            )
        if has_lat and not (-90 <= (self.geofencing_reference_lat or 0) <= 90):
            raise ValueError("geofencing_reference_lat must be between -90 and 90")
        if has_lng and not (-180 <= (self.geofencing_reference_lng or 0) <= 180):
            raise ValueError("geofencing_reference_lng must be between -180 and 180")
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
