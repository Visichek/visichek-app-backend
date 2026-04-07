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

    # Payment provider references
    stripe_customer_id: Optional[str] = None
    flutterwave_customer_id: Optional[str] = None
    default_payment_provider: Optional[str] = None  # "stripe" or "flutterwave"


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
    stripe_customer_id: Optional[str] = None
    flutterwave_customer_id: Optional[str] = None
    default_payment_provider: Optional[str] = None
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
