"""Checkout session schemas — provider-agnostic representation of a
tenant's intent to subscribe to a plan.
"""

from __future__ import annotations

from schemas.imports import *  # noqa: F401,F403
from pydantic import BaseModel, Field, model_validator
from typing import Any, List, Optional
import time

from bson import ObjectId
from enum import Enum

from schemas.subscription_schema import BillingCycle


class CheckoutStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CheckoutProvider(str, Enum):
    STRIPE = "stripe"
    FLUTTERWAVE = "flutterwave"
    APP = "app"


class PriceBreakdown(BaseModel):
    base_price: float
    billing_cycle: BillingCycle
    currency: str
    applied_discount_ids: List[str] = Field(default_factory=list)
    total_percentage_off: float = 0.0
    total_fixed_off: float = 0.0
    final_price: float
    # Minor units (cents / kobo) of ``final_price`` as charged to the provider.
    amount_minor: int


class CheckoutCreateRequest(BaseModel):
    plan_id: str
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    discount_ids: List[str] = Field(default_factory=list)
    # Optional preferred provider. If unset or unavailable, the server picks.
    preferred_provider: Optional[CheckoutProvider] = None
    # Optional trial override; defaults to 0 (no trial for a paid checkout).
    trial_days: int = 0
    # When set, the checkout redeems the tenant's one-time trial code.
    # The server forces amount_minor=0 and uses the trial's snapshot length;
    # the code is only marked USED once the $0 checkout actually clears.
    # Mutually exclusive with ``discount_ids`` — a trial is itself a
    # full-value discount and the server rejects combining the two.
    trial_code: Optional[str] = None
    # Optional admin-supplied metadata to attach to the session record.
    metadata: Optional[dict] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.trial_days < 0:
            raise ValueError("trial_days must be non-negative")
        if self.trial_code and self.discount_ids:
            raise ValueError(
                "trial_code cannot be combined with discount_ids"
            )
        return self


class CheckoutCompleteRequest(BaseModel):
    """Body for the app-mode completion endpoint."""

    outcome: str = Field(description="'success' or 'failure'")

    @model_validator(mode="after")
    def _validate(self):
        if self.outcome not in ("success", "failure"):
            raise ValueError("outcome must be 'success' or 'failure'")
        return self


class CheckoutSessionBase(BaseModel):
    tenant_id: str
    plan_id: str
    billing_cycle: BillingCycle
    currency: str
    amount_minor: int
    provider: CheckoutProvider
    status: CheckoutStatus = CheckoutStatus.PENDING
    checkout_url: str
    # Provider-assigned reference (e.g. stripe PaymentIntent id, flutterwave
    # tx_ref, or the app-mode uuid). Used to correlate webhooks.
    provider_reference: str
    # Opaque payload returned by the provider (client_secret, raw checkout body).
    provider_payload: dict = Field(default_factory=dict)
    # Price breakdown for UI display and audit.
    breakdown: PriceBreakdown
    # Discount IDs validated at checkout creation time.
    applied_discount_ids: List[str] = Field(default_factory=list)
    # Identity of the super_admin that initiated the checkout.
    created_by_user_id: str
    expires_at: int
    completed_at: Optional[int] = None
    # Set only once payment succeeds and the subscription is provisioned.
    subscription_id: Optional[str] = None
    failure_reason: Optional[str] = None
    metadata: Optional[dict] = None
    trial_days: int = 0
    # Set when this checkout is redeeming a trial code. The string here is
    # the literal code value (not the row id) so the completion path can
    # look up + mark it USED without an extra round trip.
    trial_code: Optional[str] = None


class CheckoutSessionCreate(CheckoutSessionBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckoutSessionUpdate(BaseModel):
    status: Optional[CheckoutStatus] = None
    completed_at: Optional[int] = None
    subscription_id: Optional[str] = None
    failure_reason: Optional[str] = None
    provider_payload: Optional[dict] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckoutSessionOut(CheckoutSessionBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values: Any):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
