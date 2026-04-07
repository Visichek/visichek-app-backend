from schemas.imports import *
from pydantic import Field
import time


class VisitorProfileBase(BaseModel):
    tenant_id: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: str
    company: Optional[str] = None
    photo_url: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    id_image_url: Optional[str] = None
    profiling_preference: str = "allowed"  # "allowed" or "opted_out"
    last_verified_at: Optional[int] = None


class VisitorProfileCreate(VisitorProfileBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorProfileUpdate(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    company: Optional[str] = None
    photo_url: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    id_image_url: Optional[str] = None
    profiling_preference: Optional[str] = None
    last_verified_at: Optional[int] = None
    deleted_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class VisitorProfileOut(VisitorProfileBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    deleted_at: Optional[int] = None
    total_visits: Optional[int] = 0
    last_visit_date: Optional[int] = None

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
