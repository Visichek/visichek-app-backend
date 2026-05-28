"""Per-tenant agreement acceptance record.

One row per ``(tenant_id, agreement_key)`` in ``tenant_agreements``. While
unaccepted (or accepted at an older master version) the copy is rebuilt from
the current master template + the tenant's current details on every read; on
acceptance the resolved ``body`` is frozen as the immutable record of exactly
what the tenant agreed to and at which master ``version``.

This unified store replaces the bespoke ``tenant_dpa_agreements`` collection —
the DPA is now one ``agreement_key`` among others.
"""

from schemas.imports import *  # noqa: F401,F403  (BaseModel, ObjectId, Optional, ...)
from typing import Any, Dict, List
from pydantic import Field
import time


class TenantAgreementBase(BaseModel):
    tenant_id: str
    # Stable agreement identifier from services.tenant_agreements.config.
    agreement_key: str
    # The reserved legal-document slug the master came from.
    master_slug: str
    # Master ``current_version`` (int) this copy was built from. Re-acceptance
    # is required when the master is published past this number.
    version: int = 0
    title: str
    summary: Optional[str] = None
    # Flattened plain-text projection of ``body`` for non-rich consumers.
    full_text: Optional[str] = None
    # Canonical BlockNote content ({id, type, props, content, children}) with
    # every allowlisted [placeholder] substituted from the tenant's details.
    body: List[Dict[str, Any]] = Field(default_factory=list)
    accepted: bool = False
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None
    # Last time the tenant explicitly declined this version (gate stays active).
    declined_at: Optional[int] = None


class TenantAgreementCreate(TenantAgreementBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantAgreementUpdate(BaseModel):
    version: Optional[int] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: Optional[List[Dict[str, Any]]] = None
    accepted: Optional[bool] = None
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None
    declined_at: Optional[int] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantAgreementOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    tenant_id: str
    agreement_key: str
    master_slug: Optional[str] = None
    version: int = 0
    title: str
    summary: Optional[str] = None
    full_text: Optional[str] = None
    body: List[Dict[str, Any]] = Field(default_factory=list)
    accepted: bool = False
    accepted_at: Optional[int] = None
    accepted_by: Optional[str] = None
    declined_at: Optional[int] = None
    # Exposed on the wire as createdAt / updatedAt (frontend contract).
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
