"""Per-tenant Data Processing Agreement record.

Each tenant gets its own DPA copy (stored in ``tenant_dpa_agreements``) built
from the committed template with the Organization party block filled in from
the tenant's details. Before acceptance the copy is refreshed from the current
tenant fields on read; on acceptance the resolved body is frozen as the
immutable record of exactly what the tenant agreed to.
"""

from schemas.imports import *
from typing import Any, Dict, List
from pydantic import Field
import time


class DpaAgreementBase(BaseModel):
    tenant_id: str
    # DPA text version in force (mirrors services.tenant_service.CURRENT_DPA_VERSION).
    version: str
    title: str
    summary: Optional[str] = None
    # Flattened plain-text projection of ``body`` for non-rich consumers.
    full_text: Optional[str] = None
    # The canonical BlockNote content blocks ({id, type, props, content, children}).
    body: List[Dict[str, Any]] = Field(default_factory=list)
    # Acceptance state. ``accepted`` mirrors tenant.dpa_accepted but scoped to
    # this exact resolved copy + version.
    accepted: bool = False
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None


class DpaAgreementCreate(DpaAgreementBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DpaAgreementUpdate(BaseModel):
    version: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: Optional[List[Dict[str, Any]]] = None
    accepted: Optional[bool] = None
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DpaAgreementOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: str
    version: str
    title: str
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: List[Dict[str, Any]] = Field(default_factory=list)
    accepted: bool = False
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None
    # Exposed on the wire as createdAt / updatedAt (the frontend contract).
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict):
            if "_id" in values and isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
            if (
                values.get("created_at") is None
                and values.get("date_created") is not None
            ):
                values["created_at"] = values["date_created"]
            if (
                values.get("updated_at") is None
                and values.get("last_updated") is not None
            ):
                values["updated_at"] = values["last_updated"]
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
