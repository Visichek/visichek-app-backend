from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class OperationType(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


class UsageRecordBase(BaseModel):
    """Tracks a single usage event for quota enforcement."""
    tenant_id: str
    subscription_id: str
    collection: str  # resource type: "visitors", "appointments", etc.
    operation: OperationType
    endpoint: Optional[str] = None  # full endpoint path
    user_id: Optional[str] = None  # system user who triggered
    user_role: Optional[str] = None


class UsageRecordCreate(UsageRecordBase):
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class UsageRecordOut(UsageRecordBase):
    id: Optional[str] = Field(default=None, alias="_id")
    timestamp: Optional[int] = None

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


class UsageAggregateBase(BaseModel):
    """Pre-aggregated usage counters for fast quota checking.
    One document per tenant+collection+operation+period.
    """
    tenant_id: str
    subscription_id: str
    collection: str
    operation: OperationType
    period_key: str  # e.g. "2026-04" for monthly, "2026-04-07" for daily
    count: int = 0


class UsageAggregateCreate(UsageAggregateBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class UsageAggregateUpdate(BaseModel):
    count: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class UsageAggregateOut(UsageAggregateBase):
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


class TenantUsageSummary(BaseModel):
    """Summary view of a tenant's current usage vs limits."""
    tenant_id: str
    plan_name: str
    plan_tier: str
    subscription_status: str
    period: str  # e.g. "2026-04"
    crud_usage: dict  # collection -> {create: {used, limit}, update: {used, limit}, ...}
    retrieval_usage: dict  # collection -> {read: {used, limit}}
    entity_counts: dict  # e.g. {system_users: 5, departments: 3, ...}
    entity_caps: dict  # e.g. {max_system_users: 10, max_departments: 5, ...}
    storage: dict  # {documents_used, documents_limit, storage_mb_used, storage_mb_limit}
