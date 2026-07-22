from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class PlanTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"
    # ── Legacy ──
    # Kept for backwards compatibility with old subscription / plan
    # documents written before the canonical tier overhaul. New plans
    # MUST use one of the four canonical tiers above. The migration in
    # ``services.plan_bootstrap`` archives any plan still using these.
    PROFESSIONAL = "professional"
    CUSTOM = "custom"


class PlanStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DRAFT = "draft"


class QuotaResetInterval(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    NEVER = "never"  # lifetime cap


class FeatureRule(BaseModel):
    """Controls access to a specific endpoint/feature."""

    endpoint_pattern: str  # e.g. "/v1/visitors/*" or "/v1/appointments"
    methods: List[str] = Field(default_factory=lambda: ["GET", "POST", "PUT", "DELETE"])
    enabled: bool = True
    description: Optional[str] = None


class CrudLimit(BaseModel):
    """Limits on create/update/delete operations for a resource collection."""

    collection: str  # e.g. "visitors", "appointments", "departments"
    max_create: Optional[int] = None  # None = unlimited
    max_update: Optional[int] = None
    max_delete: Optional[int] = None
    reset_interval: QuotaResetInterval = QuotaResetInterval.MONTHLY
    description: Optional[str] = None


class RetrievalQuota(BaseModel):
    """Limits on how many read/list operations per interval."""

    collection: str  # e.g. "visitors", "dashboard", "audit_logs"
    max_reads: Optional[int] = None  # None = unlimited
    reset_interval: QuotaResetInterval = QuotaResetInterval.DAILY
    description: Optional[str] = None


class StorageLimit(BaseModel):
    """Storage limits for the tenant."""

    max_documents: Optional[int] = None  # total document uploads
    max_storage_mb: Optional[int] = None  # total storage in MB
    max_file_size_mb: int = 10  # per-file size cap


class TenantCapLimit(BaseModel):
    """Hard caps on entity counts within the tenant."""

    max_system_users: Optional[int] = None
    max_departments: Optional[int] = None
    max_branches: Optional[int] = None
    max_visitors_per_month: Optional[int] = None
    max_appointments_per_month: Optional[int] = None
    visitors_per_branch_per_month: Optional[int] = None


class PlanBase(BaseModel):
    name: str
    display_name: str
    tier: PlanTier = PlanTier.FREE
    description: Optional[str] = None
    status: PlanStatus = PlanStatus.DRAFT

    # Pricing (internal tracking, no payment integration)
    base_price_monthly: float = 0.0
    base_price_yearly: float = 0.0
    currency: str = "NGN"

    # Feature flags: list of endpoint patterns allowed/denied
    feature_rules: List[FeatureRule] = Field(default_factory=list)

    # CRUD operation limits per reset interval
    crud_limits: List[CrudLimit] = Field(default_factory=list)

    # Retrieval/read quotas
    retrieval_quotas: List[RetrievalQuota] = Field(default_factory=list)

    # Storage limits
    storage_limits: StorageLimit = Field(default_factory=StorageLimit)

    # Hard caps on tenant entities
    tenant_caps: TenantCapLimit = Field(default_factory=TenantCapLimit)

    # Priority support, SLA, branding
    priority_support: bool = False
    sla_response_hours: Optional[int] = None
    custom_branding: bool = False
    api_access: bool = False

    # Trial period in days. ``0`` means the plan does not offer a trial.
    # When > 0, tenants can claim a one-time trial code via
    # ``POST /v1/trials/claim?plan_id=...`` and start a $0 checkout that
    # converts to ACTIVE (or downgrades back to free) at ``trial_ends_at``.
    trial_days: int = 0

    # Support-case tier — controls admin paging on support threads.
    # NONE: admins only see the case in the dashboard, no email blast.
    # STANDARD: admin emails on case-open + SLA breaches.
    # PRIORITY: admin emails on every event + per-tenant-reply pings.
    support_tier: SupportTier = SupportTier.NONE

    # Whether this plan is visible on the public plan listing
    is_public: bool = True

    # Sort order for display
    sort_order: int = 0


class PlanCreate(PlanBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if self.base_price_monthly < 0:
            raise ValueError("base_price_monthly must be non-negative")
        if self.base_price_yearly < 0:
            raise ValueError("base_price_yearly must be non-negative")
        if self.trial_days < 0:
            raise ValueError("trial_days must be non-negative")
        # Ensure plan name is URL-safe slug
        if not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "Plan name must be alphanumeric with hyphens/underscores only"
            )
        return self


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    tier: Optional[PlanTier] = None
    description: Optional[str] = None
    status: Optional[PlanStatus] = None
    base_price_monthly: Optional[float] = None
    base_price_yearly: Optional[float] = None
    currency: Optional[str] = None
    feature_rules: Optional[List[FeatureRule]] = None
    crud_limits: Optional[List[CrudLimit]] = None
    retrieval_quotas: Optional[List[RetrievalQuota]] = None
    storage_limits: Optional[StorageLimit] = None
    tenant_caps: Optional[TenantCapLimit] = None
    priority_support: Optional[bool] = None
    sla_response_hours: Optional[int] = None
    custom_branding: Optional[bool] = None
    api_access: Optional[bool] = None
    support_tier: Optional[SupportTier] = None
    is_public: Optional[bool] = None
    sort_order: Optional[int] = None
    trial_days: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PlanOut(PlanBase):
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
