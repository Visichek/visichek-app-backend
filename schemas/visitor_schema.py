from __future__ import annotations

import time
from schemas.imports import *


class VisitorBase(BaseModel):
    tenant_id: str
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    bio_data: dict = Field(default_factory=dict)
    verified: bool = False
    id_number_encrypted: Optional[str] = None
    id_document_id: Optional[str] = None
    portrait_url: Optional[str] = None


class VisitorCreate(VisitorBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    bio_data: Optional[dict] = None
    verified: Optional[bool] = None
    id_number_encrypted: Optional[str] = None
    id_document_id: Optional[str] = None
    portrait_url: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorOut(VisitorBase):
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


class VisitorLookupResponse(BaseModel):
    found: bool
    visitor: Optional[VisitorOut] = None
