"""Add-on catalog + tenant-purchased add-on instances.

The add-on system lets us extend a plan without authoring a brand new
SKU per option. Storage extensions (1 GB = ₦2,000) are the first
addon kind; the model is generic enough to support more — extra
visitors per month, extra branches, etc. — without schema churn:
every addon has a ``kind`` discriminator and a small ``benefit``
payload that the consuming service interprets.

Two collections:

* ``addons``        — catalog of *available* add-ons (admin-managed).
* ``tenant_addons`` — instances *purchased* by a tenant, with
                      expiry + payment linkage.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, model_validator


class AddonKind(str, Enum):
    """What kind of resource an addon grants when active."""

    STORAGE_EXTENSION = "storage_extension"
    VISITOR_QUOTA = "visitor_quota"
    BRANCH_QUOTA = "branch_quota"


class AddonStatus(str, Enum):
    """Catalog lifecycle of an addon offering."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class TenantAddonStatus(str, Enum):
    """Per-tenant addon instance lifecycle.

    ``pending``  — payment intent created, awaiting webhook.
    ``active``   — paid, currently providing benefit.
    ``expired``  — past ``expires_at``; benefit no longer counted.
    ``cancelled``— cancelled / refunded before expiry.
    """

    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ─── Catalog (addons collection) ─────────────────────────────────────


class AddonBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    kind: AddonKind
    status: AddonStatus = AddonStatus.ACTIVE

    # Stable machine key for idempotent catalog seeding / lookup of
    # singleton addons (e.g. "additional-branch"). Optional because
    # admin-authored addons don't need one — only bootstrap-seeded
    # singletons do.
    slug: Optional[str] = Field(default=None, max_length=80)

    # Per-unit pricing. ``quantity`` on purchase multiplies both the
    # price and the benefit (so 5 units of a 1 GB / ₦2,000 addon
    # costs ₦10,000 and grants 5 GB).
    #
    # ``unit_price`` is authoritative for ``pricing_mode="fixed"``
    # addons. For ``pricing_mode="derived"`` addons it is a DISPLAY
    # CACHE only — the true price is resolved live via
    # ``services.addon_service.resolve_addon_unit_price`` from
    # ``derived_from`` and this field is refreshed opportunistically
    # (catalog seed refresh, Premium plan writes) so reads that skip
    # the live resolution still show a reasonably fresh number.
    unit_price: float = Field(ge=0)
    currency: str = Field(default="NGN", min_length=3, max_length=3)

    # "fixed" — unit_price is authoritative. "derived" — unit_price is
    # computed live from another plan's field (see derived_from).
    pricing_mode: Literal["fixed", "derived"] = "fixed"

    # Only meaningful when pricing_mode="derived". Shape:
    # {"plan": "premium", "field": "base_price_monthly", "multiplier": 0.8}
    # — resolves to round(plan.<field> * multiplier) whole currency units.
    derived_from: Optional[dict[str, Any]] = None

    # Whether this addon renews automatically (charged each cycle)
    # rather than being a one-time, time-bound purchase.
    recurring: bool = False

    # Renewal cadence for recurring addons. Ignored for one-time addons.
    billing_cycle: Literal["monthly", "yearly"] = "monthly"

    # Benefit per unit. Storage addons: ``{"storage_mb": 1024}`` for
    # the 1 GB SKU. Visitor quota addons: ``{"visitors": 500}``.
    # Branch quota addons: ``{"branches": 1}``. The kind tells the
    # consuming service which keys to read.
    benefit_per_unit: dict[str, Any] = Field(default_factory=dict)

    # Default validity in days for ONE-TIME (non-recurring) addons.
    # ``None`` = perpetual / lifetime; otherwise
    # ``expires_at = purchased_at + validity_days * 86400``. Ignored
    # for recurring addons, whose per-cycle length comes from
    # ``billing_cycle`` instead.
    validity_days: Optional[int] = 365

    # Hard cap on units per purchase (None = unlimited). Lets us cap
    # a "VIP storage" SKU to e.g. 10 GB per buy.
    max_units_per_purchase: Optional[int] = None


class AddonCreate(AddonBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AddonUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[AddonStatus] = None
    unit_price: Optional[float] = None
    currency: Optional[str] = None
    pricing_mode: Optional[Literal["fixed", "derived"]] = None
    derived_from: Optional[dict[str, Any]] = None
    recurring: Optional[bool] = None
    billing_cycle: Optional[Literal["monthly", "yearly"]] = None
    benefit_per_unit: Optional[dict[str, Any]] = None
    validity_days: Optional[int] = None
    max_units_per_purchase: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AddonOut(AddonBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _convert_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


# ─── Tenant addon instance (tenant_addons collection) ────────────────


class TenantAddonBase(BaseModel):
    tenant_id: str
    addon_id: str
    addon_kind: AddonKind  # snapshot — addon edits don't retro-change benefit
    quantity: int = Field(ge=1)
    unit_price_snapshot: float = Field(ge=0)
    currency_snapshot: str = "NGN"
    benefit_snapshot: dict[str, Any] = Field(default_factory=dict)

    # Snapshotted at purchase time so a catalog edit between purchase
    # and webhook activation can't change the granted validity/cycle
    # (mirrors unit_price_snapshot's purpose for price). For recurring
    # addons, activation computes expires_at from these — NOT from the
    # addon's CURRENT catalog row — avoiding the pre-existing
    # activate_tenant_addon_by_reference quirk.
    recurring_snapshot: bool = False
    validity_days_snapshot: Optional[int] = None
    billing_cycle_snapshot: Optional[str] = None

    status: TenantAddonStatus = TenantAddonStatus.PENDING
    purchased_at: Optional[int] = None  # set on payment success
    expires_at: Optional[int] = None  # None = perpetual

    # Renewal bookkeeping for ``recurring_snapshot=True`` rows — mirrors
    # ``SubscriptionBase``'s dunning fields. ``renewal_attempts`` counts
    # consecutive failed renewal charges since the last success;
    # ``next_retry_at`` is when the renewal sweep should retry a row
    # currently in its dunning grace window (``expires_at`` is pushed out
    # to match while a retry is pending, so the addon keeps granting
    # benefit through the grace period — see services/addon_renewal_service.py).
    renewal_attempts: int = 0
    next_retry_at: Optional[int] = None
    last_renewal_attempt_at: Optional[int] = None

    # Payment correlation: provider + reference + checkout id.
    payment_provider: Optional[str] = None
    payment_reference: Optional[str] = None
    checkout_url: Optional[str] = None
    completed_at: Optional[int] = None
    cancelled_at: Optional[int] = None
    cancellation_reason: Optional[str] = None

    created_by_user_id: Optional[str] = None

    # Free-form provenance bag. Currently used by the Task 9 grandfathering
    # backfill: ``{"granted": "premium-per-location-migration"}`` marks a
    # zero-price row minted for a pre-existing Premium tenant so the
    # backfill can skip tenants it has already processed on re-run.
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantAddonCreate(TenantAddonBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantAddonUpdate(BaseModel):
    status: Optional[TenantAddonStatus] = None
    purchased_at: Optional[int] = None
    expires_at: Optional[int] = None
    unit_price_snapshot: Optional[float] = None
    renewal_attempts: Optional[int] = None
    next_retry_at: Optional[int] = None
    last_renewal_attempt_at: Optional[int] = None
    payment_provider: Optional[str] = None
    payment_reference: Optional[str] = None
    checkout_url: Optional[str] = None
    completed_at: Optional[int] = None
    cancelled_at: Optional[int] = None
    cancellation_reason: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantAddonOut(TenantAddonBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _convert_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


# ─── Purchase API payloads ──────────────────────────────────────────


class AddonPurchaseRequest(BaseModel):
    addon_id: str
    quantity: int = Field(default=1, ge=1, le=1000)
    preferred_provider: Optional[str] = None  # "stripe" | "flutterwave" | "app"


class AddonPurchaseResponse(BaseModel):
    tenant_addon_id: str
    addon_id: str
    addon_name: str
    addon_kind: AddonKind
    quantity: int
    amount_total: float
    currency: str
    payment_provider: str
    checkout_url: str
    payment_reference: str
    status: TenantAddonStatus


# ─── Storage quota summary ──────────────────────────────────────────


class StorageQuotaOut(BaseModel):
    """Computed storage budget surfaced via GET /v1/storage/quota.

    ``plan_storage_mb`` is whatever ``plan.storage_limits.max_storage_mb``
    resolves to (None = unlimited). ``addon_storage_mb`` is the sum of
    all currently-active storage-extension addons. The frontend
    surfaces these distinctly so the user can tell which slice they're
    consuming.
    """

    tenant_id: str
    plan_storage_mb: Optional[int] = None
    addon_storage_mb: int = 0
    total_storage_mb: Optional[int] = None  # None = unlimited (plan was None)
    used_bytes: int = 0
    used_mb: float = 0.0
    remaining_mb: Optional[float] = None  # None = unlimited
    document_count: int = 0
    max_documents: Optional[int] = None
    max_file_size_mb: int = 10
    active_addons: int = 0
