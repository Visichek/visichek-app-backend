from schemas.imports import *
from pydantic import Field
import time


class TenantBase(BaseModel):
    company_name: str
    lawful_basis: LawfulBasis = LawfulBasis.LEGITIMATE_INTEREST
    notice_display_mode: NoticeDisplayMode = NoticeDisplayMode.PASSIVE
    retention_days: int = 1095  # 3 years default
    default_retention_action: DeletionAction = DeletionAction.ANONYMISE
    dpo_contact_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None
    cross_border_approved: bool = False
    is_active: bool = True
    active_notice_version: Optional[str] = None
    enable_repeat_visitor_recognition: bool = True  # Profiling preference control

    # Payment provider references
    stripe_customer_id: Optional[str] = None
    flutterwave_customer_id: Optional[str] = None
    default_payment_provider: Optional[str] = None  # "stripe" or "flutterwave"

    # 2FA / MFA policy
    mfa_default_for_users: bool = False
    mfa_user_override_allowed: bool = True

    # First-login onboarding info review.
    # True once the tenant's super_admin has reviewed and confirmed the
    # company details carried over from onboarding (see the tenant-info
    # confirmation endpoints in api/v1/onboarding_route.py). Surfaced on
    # every TenantOut so the frontend can prompt the review screen on first
    # login. This is a soft prompt — it is NOT enforced by the auth gate.
    onboarding_info_confirmed: bool = False
    onboarding_info_confirmed_at: Optional[int] = None


class TenantCreate(TenantBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantUpdate(BaseModel):
    company_name: Optional[str] = None
    lawful_basis: Optional[LawfulBasis] = None
    notice_display_mode: Optional[NoticeDisplayMode] = None
    retention_days: Optional[int] = None
    default_retention_action: Optional[DeletionAction] = None
    dpo_contact_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None
    cross_border_approved: Optional[bool] = None
    is_active: Optional[bool] = None
    active_notice_version: Optional[str] = None
    enable_repeat_visitor_recognition: Optional[bool] = None
    stripe_customer_id: Optional[str] = None
    flutterwave_customer_id: Optional[str] = None
    default_payment_provider: Optional[str] = None
    mfa_default_for_users: Optional[bool] = None
    mfa_user_override_allowed: Optional[bool] = None
    onboarding_info_confirmed: Optional[bool] = None
    onboarding_info_confirmed_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantBootstrapRequest(BaseModel):
    """Combined payload to create a tenant and its first super_admin in one step."""

    # --- Tenant fields ---
    company_name: str
    lawful_basis: LawfulBasis = LawfulBasis.LEGITIMATE_INTEREST
    notice_display_mode: NoticeDisplayMode = NoticeDisplayMode.PASSIVE
    retention_days: int = 1095
    default_retention_action: DeletionAction = DeletionAction.ANONYMISE
    dpo_contact_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None
    cross_border_approved: bool = False

    # --- First super_admin fields ---
    admin_full_name: str
    admin_email: EmailStr
    admin_password: str

    @model_validator(mode="after")
    def validate_admin_password(self):
        from security.password_policy import validate_password_strength

        result = validate_password_strength(self.admin_password)
        if not result.is_valid:
            raise ValueError("; ".join(result.errors))
        return self


class TenantBootstrapOut(BaseModel):
    """Response after bootstrapping a tenant + first super_admin."""

    tenant: "TenantOut"
    super_admin: dict  # SystemUserOut serialised (avoids circular import)


class TenantOut(TenantBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class TenantPlanSummary(BaseModel):
    """Lightweight plan + subscription snapshot attached to tenant responses."""

    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    plan_display_name: Optional[str] = None
    plan_tier: Optional[str] = None
    subscription_id: Optional[str] = None
    subscription_status: Optional[str] = None
    billing_cycle: Optional[str] = None
    effective_price: Optional[float] = None
    currency: Optional[str] = None
    current_period_end: Optional[int] = None
    trial_ends_at: Optional[int] = None
    entity_caps: Optional[dict] = None


class TenantWithSummaryOut(TenantOut):
    """TenantOut enriched with plan, subscription, and usage cap summary."""

    plan_summary: Optional[TenantPlanSummary] = None


class TenantInfoConfirmRequest(BaseModel):
    """Super_admin's first-login confirmation of their company details.

    Every editable field is Optional — omitting one keeps the value that
    was carried over from onboarding. Submitting the request (with or
    without edits) marks the tenant's onboarding info as confirmed.
    """

    company_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    dpo_contact_email: Optional[EmailStr] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None


class TenantInfoConfirmationOut(BaseModel):
    """First-login review payload.

    Carries the tenant identity fields the super_admin should eyeball,
    the confirmation status, and — best-effort — the original onboarding
    form submission (verbatim values + labels + order) so the frontend can
    render the "this is what you told us" context next to the editable
    fields. Onboarding context is ``None``/empty for tenants created via
    the legacy bootstrap path (which has no submission record).
    """

    tenant_id: str
    company_name: str
    dpo_contact_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None

    onboarding_info_confirmed: bool = False
    onboarding_info_confirmed_at: Optional[int] = None

    # Read-only onboarding context (the form the tenant originally submitted).
    onboarding_submission_id: Optional[str] = None
    onboarding_fields: dict = Field(default_factory=dict)
    onboarding_field_labels: dict = Field(default_factory=dict)
    onboarding_field_order: list = Field(default_factory=list)
