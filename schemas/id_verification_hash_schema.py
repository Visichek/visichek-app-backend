from __future__ import annotations

import time
from schemas.imports import *


class IDVerificationHashBase(BaseModel):
    tenant_id: str
    sha256: str
    visitor_id: str
    extraction_id: Optional[str] = None
    document_id: Optional[str] = None


class IDVerificationHashCreate(IDVerificationHashBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))


class IDVerificationHashOut(IDVerificationHashBase):
    id: Optional[str] = Field(default=None, alias="_id")
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
