"""Per-tenant one-time trial code schemas.

A trial code is the tenant-scoped equivalent of a discount code, but with
two key differences:

1. **Uniqueness:** each tenant has at most one *redeemed* trial across
   their whole platform lifetime. A code is generated lazily the first
   time a tenant claims a trial for a trial-supporting plan; further
   claim attempts return either the existing pending code (if the same
   plan) or a 409 (if already redeemed).
2. **Pricing:** redemption forces the checkout amount to 0. The trial
   length is snapshotted from the plan at claim time so a later admin
   edit to ``plan.trial_days`` does not change an outstanding trial.

The code itself is only marked ``USED`` when the matching checkout
transitions to ``SUCCEEDED`` (i.e. the $0 payment actually clears and a
TRIALING subscription is provisioned). Creating the checkout alone is
not enough — same semantics as discount-code redemption.
"""

from __future__ import annotations

from schemas.imports import *  # noqa: F401,F403
from pydantic import BaseModel, Field, model_validator
from typing import Any, Optional
import time

from bson import ObjectId
from enum import Enum


class TrialCodeStatus(str, Enum):
    PENDING = "pending"  # claimed but not yet redeemed
    USED = "used"  # successfully redeemed → subscription is TRIALING / converted
    CANCELLED = "cancelled"  # tenant cancelled the pending checkout before redemption


class TrialCodeBase(BaseModel):
    code: str  # unique opaque string e.g. ``TRIAL-<tenant-hash>-<rand>``
    tenant_id: str
    plan_id: str
    status: TrialCodeStatus = TrialCodeStatus.PENDING
    # Snapshot of ``plan.trial_days`` at claim time. The checkout completion
    # path uses this value, never re-reads from the plan, so an admin edit
    # to ``trial_days`` after the fact does not retroactively change the
    # tenant's outstanding trial length.
    trial_days_snapshot: int
    # Once redeemed, the subscription that the trial spun up. Lets us
    # answer "which subscription was the trial used for?" without joining.
    subscription_id: Optional[str] = None
    # When the trial was actually consumed (status -> USED).
    used_at: Optional[int] = None


class TrialCodeCreate(TrialCodeBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if self.trial_days_snapshot <= 0:
            raise ValueError("trial_days_snapshot must be positive")
        if not self.code:
            raise ValueError("code is required")
        return self


class TrialCodeUpdate(BaseModel):
    status: Optional[TrialCodeStatus] = None
    subscription_id: Optional[str] = None
    used_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TrialCodeOut(TrialCodeBase):
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


class TrialClaimResponse(BaseModel):
    """Response body for ``POST /v1/trials/claim``.

    The frontend should display the ``code`` to the tenant and then pass
    it through verbatim as ``trial_code`` on the subsequent
    ``POST /v1/checkout/sessions`` call. The plan + price preview fields
    let the UI render the trial summary without a second roundtrip.
    """

    code: str
    plan_id: str
    plan_name: str
    plan_display_name: str
    trial_days: int
    trial_ends_at_preview: (
        int  # what trial_ends_at would be if checkout were paid right now
    )
    base_price_monthly: float
    base_price_yearly: float
    currency: str
    status: TrialCodeStatus
