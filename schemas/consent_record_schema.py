from schemas.imports import *
from pydantic import Field
import time


class ConsentRecordBase(BaseModel):
    """A single visitor's acceptance of a privacy notice at check-in.

    Canonical, append-only store for consent captured on the kiosk / public
    submit paths (which write to the ``checkins`` collection and therefore have
    no ``visit_sessions`` row to carry consent on). Surfaced alongside
    ``visit_sessions`` consent in GET /v1/compliance/consent-log.
    """

    tenant_id: str
    checkin_id: Optional[str] = None
    session_id: Optional[str] = None
    visitor_id: Optional[str] = None
    visitor_name_snapshot: Optional[str] = None
    department_id: Optional[str] = None
    privacy_notice_id: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None
    consent_granted: bool = False
    consent_method: Optional[str] = None
    consent_timestamp: Optional[int] = None
    lawful_basis_at_time: Optional[LawfulBasis] = None
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None


class ConsentRecordCreate(ConsentRecordBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))


class ConsentRecordOut(ConsentRecordBase):
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
