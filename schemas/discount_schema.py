from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class DiscountType(str, Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class DiscountScope(str, Enum):
    GLOBAL = "global"  # applies to all tenants
    TENANT = "tenant"  # applies to a specific tenant
    PLAN = "plan"  # applies to a specific plan tier


class DiscountStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


class DiscountBase(BaseModel):
    code: str  # e.g. "LAUNCH50", "ENTERPRISE_DEAL"
    name: str
    description: Optional[str] = None
    discount_type: DiscountType = DiscountType.PERCENTAGE
    value: float  # percentage (0-100) or fixed amount
    scope: DiscountScope = DiscountScope.GLOBAL
    status: DiscountStatus = DiscountStatus.ACTIVE

    # Scope targets
    target_tenant_id: Optional[str] = None  # if scope == TENANT
    target_plan_ids: List[str] = Field(default_factory=list)  # if scope == PLAN

    # Validity window
    valid_from: Optional[int] = None  # Unix timestamp
    valid_until: Optional[int] = None  # Unix timestamp

    # Usage limits
    max_redemptions: Optional[int] = None  # None = unlimited
    current_redemptions: int = 0

    # Stackable with other discounts?
    stackable: bool = False

    # Minimum subscription value to qualify
    min_subscription_value: Optional[float] = None


class DiscountCreate(DiscountBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if self.discount_type == DiscountType.PERCENTAGE and (
            self.value < 0 or self.value > 100
        ):
            raise ValueError("Percentage discount must be between 0 and 100")
        if self.discount_type == DiscountType.FIXED and self.value < 0:
            raise ValueError("Fixed discount value must be non-negative")
        if self.scope == DiscountScope.TENANT and not self.target_tenant_id:
            raise ValueError("Tenant-scoped discount requires target_tenant_id")
        # Code must be uppercase alphanumeric + underscores
        if not self.code.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "Discount code must be alphanumeric with underscores/hyphens"
            )
        return self


class DiscountUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    discount_type: Optional[DiscountType] = None
    value: Optional[float] = None
    scope: Optional[DiscountScope] = None
    status: Optional[DiscountStatus] = None
    target_tenant_id: Optional[str] = None
    target_plan_ids: Optional[List[str]] = None
    valid_from: Optional[int] = None
    valid_until: Optional[int] = None
    max_redemptions: Optional[int] = None
    current_redemptions: Optional[int] = None
    stackable: Optional[bool] = None
    min_subscription_value: Optional[float] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DiscountOut(DiscountBase):
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
