from schemas.imports import *
from pydantic import Field
import time


class DeletionLogCreate(BaseModel):
    tenant_id: str
    entity_type: str
    entity_id: str
    reason: str
    action: DeletionAction
    performed_by: Optional[str] = None
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class DeletionLogOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: str
    entity_type: str
    entity_id: str
    reason: str
    action: DeletionAction
    performed_by: Optional[str] = None
    timestamp: Optional[int] = None

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
