from __future__ import annotations

import time
from typing import Any, Dict, Optional

from pydantic import Field, model_validator

from schemas.imports import BaseModel, ObjectId


class AuditLogCreate(BaseModel):
    """Document shape written to the ``audit_trail`` collection.

    Fields mirror what ``services.audit_service.record_audit_event`` constructs:
    actor identity, action verb, the polymorphic resource pair
    (``resource_type`` + ``resource_id``), tenant scope, free-form details,
    and request correlation.
    """

    actor_id: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    tenant_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class AuditLogOut(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    actor_id: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    tenant_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    timestamp: Optional[int] = None

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


from schemas.summary_schema import (  # noqa: E402
    AppointmentBriefSummary,
    BranchBriefSummary,
    DepartmentBriefSummary,
    InvoiceBriefSummary,
    PlanBriefSummary,
    SubscriptionBriefSummary,
    TenantBriefSummary,
    UserBriefSummary,
    VisitorProfileBriefSummary,
    VisitSessionBriefSummary,
)


# Discriminated union for ``resource_summary``: the concrete shape depends on
# ``resource_type`` recorded on the audit row. Listing the variants explicitly
# gives the OpenAPI schema something useful to render and lets the frontend
# switch on the embedded ``id``+``resource_type`` pair without follow-up calls.
ResourceSummary = (
    TenantBriefSummary
    | UserBriefSummary
    | PlanBriefSummary
    | SubscriptionBriefSummary
    | DepartmentBriefSummary
    | BranchBriefSummary
    | AppointmentBriefSummary
    | VisitorProfileBriefSummary
    | VisitSessionBriefSummary
    | InvoiceBriefSummary
)


class AuditLogWithSummaryOut(AuditLogOut):
    """``AuditLogOut`` enriched with snapshots for every external id.

    The frontend renders audit rows without follow-up requests: ``actor_id``,
    ``tenant_id`` and the polymorphic ``resource_id`` each carry a brief
    summary so names, statuses, and identifying metadata are present inline.
    """

    tenant_summary: Optional[TenantBriefSummary] = None
    actor_summary: Optional[UserBriefSummary] = None
    resource_summary: Optional[ResourceSummary] = None
