from schemas.imports import *
from pydantic import Field
import time


class AuditLogCreate(BaseModel):
    tenant_id: str
    actor_id: str
    actor_name_snapshot: Optional[str] = None
    user_session_id: Optional[str] = None
    action: str
    target_entity: Optional[str] = None
    target_id: Optional[str] = None
    ip: Optional[str] = None
    device_signature: Optional[str] = None
    reason: Optional[str] = None
    occurred_at: int = Field(default_factory=lambda: int(time.time()))


class AuditLogOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: str
    actor_id: str
    actor_name_snapshot: Optional[str] = None
    user_session_id: Optional[str] = None
    action: str
    target_entity: Optional[str] = None
    target_id: Optional[str] = None
    ip: Optional[str] = None
    device_signature: Optional[str] = None
    reason: Optional[str] = None
    occurred_at: Optional[int] = None

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


from schemas.summary_schema import TenantBriefSummary, UserBriefSummary  # noqa: E402


class AuditLogWithSummaryOut(AuditLogOut):
    """AuditLogOut enriched with the actor and tenant snapshots.

    ``target_id`` is intentionally left un-enriched because it can refer to
    any kind of entity (visitor, department, plan, etc.) and the type lookup
    is best handled by the frontend with the existing ``target_entity`` hint.
    """

    tenant_summary: Optional[TenantBriefSummary] = None
    actor_summary: Optional[UserBriefSummary] = None
