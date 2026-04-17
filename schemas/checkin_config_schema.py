from __future__ import annotations

import time
from schemas.imports import *


class CheckinFieldDef(BaseModel):
    key: str
    label: str
    type: str
    required: bool = False
    category: CheckinFieldCategory
    options_endpoint: Optional[str] = None
    options: Optional[list[dict]] = None


class CheckinConfigBase(BaseModel):
    tenant_id: str
    required_fields: list[CheckinFieldDef]
    id_upload_enabled: bool = True
    allow_returning_visitor_lookup: bool = True
    active: bool = True


class CheckinConfigCreate(CheckinConfigBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinConfigUpdate(BaseModel):
    required_fields: Optional[list[CheckinFieldDef]] = None
    id_upload_enabled: Optional[bool] = None
    allow_returning_visitor_lookup: Optional[bool] = None
    active: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class CheckinConfigOut(CheckinConfigBase):
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


class PublicCheckinConfigOut(BaseModel):
    checkin_config_id: str
    tenant_id: str
    tenant_name: str
    logo_url: Optional[str] = None
    id_upload_enabled: bool
    allow_returning_visitor_lookup: bool
    required_fields: list[CheckinFieldDef]
