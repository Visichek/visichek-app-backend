from schemas.imports import *
from pydantic import Field
import time


class DPRBase(BaseModel):
    tenant_id: str
    field_name: str
    purpose: str
    lawful_basis: LawfulBasis
    retention_period: Optional[str] = None
    sub_processor_id: Optional[str] = None
    crosses_borders: bool = False


class DPRCreate(DPRBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))


class DPROut(DPRBase):
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
