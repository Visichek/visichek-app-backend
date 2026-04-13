from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class BranchStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class BranchBase(BaseModel):
    """A physical or logical location within a tenant's organisation."""

    tenant_id: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_headquarters: bool = False
    status: BranchStatus = BranchStatus.ACTIVE
    metadata: Optional[dict] = None


class BranchCreate(BranchBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if not self.name or not self.name.strip():
            raise ValueError("Branch name is required")
        if not self.tenant_id or not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        return self


class BranchUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_headquarters: Optional[bool] = None
    status: Optional[BranchStatus] = None
    metadata: Optional[dict] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class BranchOut(BranchBase):
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
