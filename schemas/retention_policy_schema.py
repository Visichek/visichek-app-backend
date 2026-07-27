from schemas.imports import *
from pydantic import Field
import time


class RetentionPolicyBase(BaseModel):
    tenant_id: str
    scope: RetentionScope
    retention_days: int
    action: DeletionAction = DeletionAction.ANONYMISE


class RetentionPolicyCreate(RetentionPolicyBase):
    # tenant_id is token-derived and injected by the route; never client-supplied.
    tenant_id: str = ""
    date_created: int = Field(default_factory=lambda: int(time.time()))


class RetentionPolicyUpdate(BaseModel):
    retention_days: Optional[int] = None
    action: Optional[DeletionAction] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class RetentionPolicyOut(RetentionPolicyBase):
    id: Optional[str] = Field(default=None, alias="_id")
    # Deliberately widened back to ``str``: legacy rows may carry a scope
    # that predates (or typos) the enum, and a read path must never 500 on
    # historical data. Writes are validated strictly via the Base/Create.
    scope: str  # type: ignore[assignment]
    date_created: Optional[int] = None

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
