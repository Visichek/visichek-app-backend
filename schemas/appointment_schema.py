from schemas.imports import *
from pydantic import Field
import time


class AppointmentBase(BaseModel):
    tenant_id: str
    visitor_profile_id: Optional[str] = None
    host_id: str
    department_id: str
    visitor_name_snapshot: Optional[str] = None
    host_name_snapshot: Optional[str] = None
    scheduled_datetime: int
    purpose: Optional[str] = None
    status: AppointmentStatus = AppointmentStatus.SCHEDULED


class AppointmentCreate(AppointmentBase):
    created_by: Optional[str] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AppointmentUpdate(BaseModel):
    visitor_profile_id: Optional[str] = None
    status: Optional[AppointmentStatus] = None
    scheduled_datetime: Optional[int] = None
    purpose: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AppointmentOut(AppointmentBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_by: Optional[str] = None
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

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
