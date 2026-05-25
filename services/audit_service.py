from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from bson import ObjectId

from core.background_tasks import fire_and_forget
from core.database import db
from repositories.audit_log_repo import get_audit_logs
from schemas.audit_log_schema import AuditLogOut, AuditLogWithSummaryOut
from schemas.summary_schema import UserBriefSummary

logger = logging.getLogger(__name__)

AUDIT_TRAIL_COLLECTION = "audit_trail"


async def _insert_audit_event(event_doc: Dict[str, Any]) -> Optional[str]:
    try:
        result = await db[AUDIT_TRAIL_COLLECTION].insert_one(event_doc)
        return str(result.inserted_id)
    except Exception as e:
        logger.error(
            "Failed to record audit event: action=%s resource=%s error=%s",
            event_doc.get("action"),
            event_doc.get("resource_id"),
            str(e),
        )
        return None


async def record_audit_event(
    actor_id: str,
    actor_role: str,
    action: str,
    resource_type: str,
    resource_id: str,
    tenant_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Optional[str]:
    """Record an admin/system action to the audit trail collection.

    The Mongo insert is scheduled on the event loop as a background task and
    this coroutine returns immediately. Existing callers wrap this in
    ``try/except: pass`` and never inspect the return value, so firing the
    write asynchronously removes 5–30 ms of per-request overhead. The
    lifespan drains pending background tasks on shutdown so the trail isn't
    truncated on graceful reload.
    """
    event_doc = {
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "tenant_id": tenant_id,
        "details": details or {},
        "timestamp": int(time.time()),
        "request_id": request_id,
    }
    fire_and_forget(_insert_audit_event(event_doc), name=f"audit.{action}")
    return None


# ---------------------------------------------------------------------------
# Read auditing
# ---------------------------------------------------------------------------
#
# Tenant-scoped GET requests are recorded to the audit trail so that
# "who viewed what, when?" is answerable for any tenant resource — not just
# mutations. This is wired into ``PlanEnforcementMiddleware`` (which already
# decodes the token, knows the tenant and the resource collection, and maps
# GET -> "read"), so reads are audited centrally without touching every route.
# Writes are NOT audited here — they are recorded in their queued writers.

# Collections whose reads we deliberately do NOT audit: the audit trail itself
# (reading it would generate a read row, which would then be read, …) and the
# high-frequency dashboard polls that would otherwise flood the trail.
_READ_AUDIT_SKIP_COLLECTIONS = {"audit", "dashboard"}

_OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def _resource_id_from_path(path: str) -> str:
    """Return the trailing ObjectId-looking path segment, else "".

    Detail reads (``/v1/privacy-notices/{id}``) carry the id in the path; list
    reads (``/v1/privacy-notices``) and named sub-paths (``/active``) do not,
    so we record an empty resource_id for those.
    """
    for segment in reversed(path.strip("/").split("/")):
        if _OBJECT_ID_RE.match(segment):
            return segment
    return ""


def should_audit_read(collection: Optional[str]) -> bool:
    """Whether a read of ``collection`` should be written to the audit trail."""
    return bool(collection) and collection not in _READ_AUDIT_SKIP_COLLECTIONS


async def record_read_audit(
    *,
    actor_id: str,
    actor_role: str,
    tenant_id: Optional[str],
    collection: str,
    path: str,
    request_id: Optional[str] = None,
) -> None:
    """Record a tenant-scoped read (GET) to the audit trail.

    Fire-and-forget and best-effort: skipped for excluded collections and never
    raises (delegates to ``record_audit_event``, which schedules the insert as
    a background task).
    """
    if not should_audit_read(collection):
        return
    await record_audit_event(
        actor_id=actor_id,
        actor_role=actor_role,
        action=f"{collection}.read",
        resource_type=collection,
        resource_id=_resource_id_from_path(path),
        tenant_id=tenant_id,
        details={"method": "GET", "path": path, "access": "read"},
        request_id=request_id,
    )


async def get_audit_trail(
    filter_dict: Optional[Dict[str, Any]] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieve raw audit-trail documents (dicts) with optional filtering."""
    try:
        cursor = (
            db[AUDIT_TRAIL_COLLECTION]
            .find(filter_dict or {})
            .sort("timestamp", -1)
            .skip(skip)
            .limit(limit)
        )
        events: List[Dict[str, Any]] = []
        async for doc in cursor:
            if "_id" in doc and isinstance(doc["_id"], ObjectId):
                doc["_id"] = str(doc["_id"])
            events.append(doc)
        return events
    except Exception as e:
        logger.error("Failed to retrieve audit trail: error=%s", str(e))
        return []


async def get_audit_trail_for_tenant(
    tenant_id: str,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return await get_audit_trail(
        filter_dict={"tenant_id": tenant_id}, skip=skip, limit=limit
    )


async def get_audit_trail_for_resource(
    resource_type: str,
    resource_id: str,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return await get_audit_trail(
        filter_dict={"resource_type": resource_type, "resource_id": resource_id},
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Summary-enriched retrieval
# ---------------------------------------------------------------------------


async def _resolve_resource_summary(
    resource_type: Optional[str], resource_id: Optional[str]
) -> Any:
    """Dispatch to the right summary resolver based on ``resource_type``.

    Audit rows record any kind of entity (subscription, plan, tenant,
    visitor, …) so we look up the resolver by type. Missing types and
    failed lookups quietly return ``None`` — enrichment is best-effort.
    """
    if not resource_type or not resource_id:
        return None

    from services import summary_resolver as sr

    dispatch: Dict[str, Callable[[str], Awaitable[Any]]] = {
        "tenant": sr.resolve_tenant_summary,
        "plan": sr.resolve_plan_summary,
        "subscription": sr.resolve_subscription_summary,
        "department": sr.resolve_department_summary,
        "branch": sr.resolve_branch_summary,
        "appointment": sr.resolve_appointment_summary,
        "visitor_profile": sr.resolve_visitor_profile_summary,
        "visit_session": sr.resolve_visit_session_summary,
        "invoice": sr.resolve_invoice_summary,
        "system_user": sr.resolve_system_user_summary,
        "admin": sr.resolve_admin_summary,
        "user": sr.resolve_user_summary,
    }

    resolver = dispatch.get(resource_type)
    if resolver is None:
        return None
    try:
        return await resolver(resource_id)
    except Exception:
        return None


def _user_type_from_role(actor_role: Optional[str]) -> Optional[str]:
    if not actor_role:
        return None
    if actor_role == "admin":
        return "admin"
    return "system_user"


async def enrich_audit_logs(
    logs: List[AuditLogOut],
) -> List[AuditLogWithSummaryOut]:
    """Attach actor / tenant / resource summaries to a page of audit logs.

    Actor and tenant summaries are batch-resolved across the whole page (one
    ``$in`` query per collection) so a 25-row page that points at a handful of
    distinct actors costs a handful of lookups, not 25. Resource summaries
    stay per-row because audit resources are polymorphic and typically
    distinct per row; they fan out concurrently.

    A deleted/missing actor yields a minimal ``{id, role, user_type:"deleted"}``
    summary rather than ``None`` so the frontend can still render the role
    instead of falling all the way back to the bare ObjectId.
    """
    if not logs:
        return []

    from services.summary_resolver import (
        resolve_tenant_summaries_batch,
        resolve_user_summaries_batch,
    )

    actor_types: Dict[str, Optional[str]] = {}
    for log in logs:
        if log.actor_id:
            actor_types[log.actor_id] = _user_type_from_role(log.actor_role)
    tenant_ids = [log.tenant_id for log in logs if log.tenant_id]

    actor_map, tenant_map, resource_summaries = await asyncio.gather(
        resolve_user_summaries_batch(actor_types),
        resolve_tenant_summaries_batch(tenant_ids),
        asyncio.gather(
            *[
                _resolve_resource_summary(log.resource_type, log.resource_id)
                for log in logs
            ]
        ),
    )

    enriched: List[AuditLogWithSummaryOut] = []
    for log, resource_summary in zip(logs, resource_summaries):
        actor_summary = actor_map.get(log.actor_id) if log.actor_id else None
        if actor_summary is None and log.actor_id:
            actor_summary = UserBriefSummary(
                id=log.actor_id,
                role=log.actor_role,
                user_type="deleted",
            )
        data = log.model_dump(by_alias=False)
        data["tenant_summary"] = (
            tenant_map.get(log.tenant_id) if log.tenant_id else None
        )
        data["actor_summary"] = actor_summary
        data["resource_summary"] = resource_summary
        enriched.append(AuditLogWithSummaryOut(**data))
    return enriched


async def retrieve_audit_logs_with_summary(
    filter_dict: Dict[str, Any],
    start: int = 0,
    stop: int = 100,
) -> List[AuditLogWithSummaryOut]:
    """Retrieve audit logs with actor + tenant + resource summaries embedded."""
    logs = await get_audit_logs(filter_dict, start=start, stop=stop)
    return await enrich_audit_logs(logs)
