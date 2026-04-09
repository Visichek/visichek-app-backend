from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionBase(BaseModel):
    tenant_id: str
    plan_id: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    billing_cycle: BillingCycle = BillingCycle.MONTHLY

    # Effective pricing after discounts (internal tracking)
    effective_price: float = 0.0
    currency: str = "NGN"

    # Trial period
    trial_ends_at: Optional[int] = None  # Unix timestamp

    # Billing period
    current_period_start: int = Field(default_factory=lambda: int(time.time()))
    current_period_end: Optional[int] = None  # Unix timestamp

    # Plan overrides: tenant-specific feature/limit adjustments
    # These MERGE with the plan defaults (override wins)
    feature_overrides: Optional[dict] = None  # endpoint_pattern -> {enabled: bool}
    crud_limit_overrides: Optional[dict] = None  # collection -> {max_create, max_update, ...}
    retrieval_quota_overrides: Optional[dict] = None  # collection -> {max_reads}
    tenant_cap_overrides: Optional[dict] = None  # field -> value

    # Discount IDs applied to this subscription
    applied_discount_ids: List[str] = Field(default_factory=list)

    # Cancellation tracking
    cancelled_at: Optional[int] = None
    cancellation_reason: Optional[str] = None

    # Internal notes (admin-only)
    admin_notes: Optional[str] = None

    # Renewal tracking
    renewal_attempts: int = 0
    last_renewal_attempt_at: Optional[int] = None
    next_retry_at: Optional[int] = None
    payment_method_id: Optional[str] = None  # stored card/payment method reference


class SubscriptionCreate(SubscriptionBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if self.effective_price < 0:
            raise ValueError("effective_price must be non-negative")
        return self


class SubscriptionUpdate(BaseModel):
    plan_id: Optional[str] = None
    status: Optional[SubscriptionStatus] = None
    billing_cycle: Optional[BillingCycle] = None
    effective_price: Optional[float] = None
    currency: Optional[str] = None
    trial_ends_at: Optional[int] = None
    current_period_start: Optional[int] = None
    current_period_end: Optional[int] = None
    feature_overrides: Optional[dict] = None
    crud_limit_overrides: Optional[dict] = None
    retrieval_quota_overrides: Optional[dict] = None
    tenant_cap_overrides: Optional[dict] = None
    applied_discount_ids: Optional[List[str]] = None
    cancelled_at: Optional[int] = None
    cancellation_reason: Optional[str] = None
    admin_notes: Optional[str] = None
    renewal_attempts: Optional[int] = None
    last_renewal_attempt_at: Optional[int] = None
    next_retry_at: Optional[int] = None
    payment_method_id: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class SubscriptionOut(SubscriptionBase):
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


class SubscriptionTenantInfo(BaseModel):
    """Tenant snapshot embedded in subscription responses."""
    id: str
    company_name: str
    is_active: bool
    country_of_hosting: Optional[str] = None
    dpo_contact_email: Optional[str] = None
    default_payment_provider: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    flutterwave_customer_id: Optional[str] = None


class SubscriptionPlanInfo(BaseModel):
    """Plan snapshot embedded in subscription responses."""
    id: str
    name: str
    display_name: str
    tier: str
    description: Optional[str] = None
    base_price_monthly: float
    base_price_yearly: float
    currency: str
    priority_support: bool
    custom_branding: bool
    api_access: bool
    tenant_caps: Optional[dict] = None


class SubscriptionWithDetailsOut(SubscriptionOut):
    """SubscriptionOut enriched with full tenant and plan info."""
    tenant: Optional[SubscriptionTenantInfo] = None
    plan: Optional[SubscriptionPlanInfo] = None
