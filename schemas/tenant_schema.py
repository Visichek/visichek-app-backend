from schemas.imports import *
from pydantic import Field
import time


class TenantBase(BaseModel):
    name: str
    lawful_basis: LawfulBasis = LawfulBasis.LEGITIMATE_INTEREST
    notice_display_mode: NoticeDisplayMode = NoticeDisplayMode.PASSIVE
    retention_days: int = 1095  # 3 years default
    default_deletion_action: DeletionAction = DeletionAction.ANONYMISE
    dpo_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    hosting_country: Optional[str] = None
    cross_border_approved: bool = False
    is_active: bool = True


class TenantCreate(TenantBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    lawful_basis: Optional[LawfulBasis] = None
    notice_display_mode: Optional[NoticeDisplayMode] = None
    retention_days: Optional[int] = None
    default_deletion_action: Optional[DeletionAction] = None
    dpo_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    hosting_country: Optional[str] = None
    cross_border_approved: Optional[bool] = None
    is_active: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


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
