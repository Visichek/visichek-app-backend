from schemas.imports import *
from pydantic import Field
import time


class SubProcessorBase(BaseModel):
    tenant_id: str
    provider: str
    purpose: str
    jurisdiction: Optional[str] = None
    dpa_signed: bool = False
    uses_data_for_training: bool = False


class SubProcessorCreate(SubProcessorBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))


class SubProcessorUpdate(BaseModel):
    provider: Optional[str] = None
    purpose: Optional[str] = None
    jurisdiction: Optional[str] = None
    dpa_signed: Optional[bool] = None
    uses_data_for_training: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class SubProcessorOut(SubProcessorBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None

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
