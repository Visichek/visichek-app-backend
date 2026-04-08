from schemas.imports import *
from pydantic import Field
import time


class IncidentLogBase(BaseModel):
    tenant_id: str
    reported_by: str
    incident_type: IncidentType
    status: IncidentStatus = IncidentStatus.OPEN
    description: str
    risk_level: Optional[str] = None  # low, medium, high, critical
    data_affected: Optional[str] = None
    mitigation_steps: Optional[str] = None
    ndpc_notified: bool = False
    ndpc_notified_at: Optional[int] = None
    detection_time: Optional[int] = None
    notification_deadline: Optional[int] = None
    notification_sent_at: Optional[int] = None


class IncidentLogCreate(IncidentLogBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode='after')
    def set_notification_deadline(self):
        if self.notification_deadline is None:
            self.notification_deadline = self.date_created + (72 * 3600)
        return self


class IncidentLogUpdate(BaseModel):
    status: Optional[IncidentStatus] = None
    description: Optional[str] = None
    risk_level: Optional[str] = None
    data_affected: Optional[str] = None
    mitigation_steps: Optional[str] = None
    ndpc_notified: Optional[bool] = None
    ndpc_notified_at: Optional[int] = None
    notification_deadline: Optional[int] = None
    notification_sent_at: Optional[int] = None
    resolved_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class IncidentLogOut(IncidentLogBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    notification_deadline: Optional[int] = None
    notification_sent_at: Optional[int] = None
    resolved_at: Optional[int] = None

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
