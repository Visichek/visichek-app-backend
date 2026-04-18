from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from bson import ObjectId

from core.background_tasks import fire_and_forget
from core.database import db
from repositories.audit_log_repo import create_audit_log, get_audit_logs
from schemas.audit_log_schema import AuditLogCreate, AuditLogOut, AuditLogWithSummaryOut

logger = logging.getLogger(__name__)

AUDIT_TRAIL_COLLECTION = "audit_trail"


async def log_action(
    tenant_id: str,
    actor_id: str,
    action: str,
    actor_name_snapshot: str | None = None,
    target_entity: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    device_signature: str | None = None,
    reason: str | None = None,
) -> None:
    """Record an admin/system action in the immutable audit log."""
    try:
        await create_audit_log(
            AuditLogCreate(
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_name_snapshot=actor_name_snapshot,
                action=action,
                target_entity=target_entity,
                target_id=target_id,
                ip=ip,
                device_signature=device_signature,
                reason=reason,
            )
        )
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}")


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

    The ``async`` signature is preserved for call-site compatibility, but the
    actual Mongo insert is scheduled on the event loop as a background task
    and this coroutine returns immediately. Every existing caller wraps this
    in ``try/except: pass`` and never inspects the return value, so firing
    the write asynchronously removes 5–30 ms of per-request overhead without
    any observable behaviour change.

    On process shutdown the lifespan drains pending background tasks so the
    trail isn't silently truncated on graceful reload.
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


async def get_audit_trail(
    filter_dict: Dict[str, Any],
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieve audit trail events with optional filtering.

    Args:
        filter_dict: MongoDB filter criteria
        skip: Number of records to skip (offset)
        limit: Maximum number of records to return

    Returns:
        List of audit event documents (dicts)
    """
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = (
            db[AUDIT_TRAIL_COLLECTION]
            .find(filter_dict)
            .sort("timestamp", -1)  # Most recent first
            .skip(skip)
            .limit(limit)
        )
        events = []
        async for doc in cursor:
            # Convert ObjectId to string for JSON serialization
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
    """Retrieve audit trail events for a specific tenant."""
    return await get_audit_trail(
        filter_dict={"tenant_id": tenant_id},
        skip=skip,
        limit=limit,
    )


async def get_audit_trail_for_resource(
    resource_type: str,
    resource_id: str,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieve audit trail events for a specific resource."""
    return await get_audit_trail(
        filter_dict={"resource_type": resource_type, "resource_id": resource_id},
        skip=skip,
        limit=limit,
    )


async def _enrich_audit_log(log: AuditLogOut) -> AuditLogWithSummaryOut:
    """Build a summary-enriched view of a single audit log entry."""
    import asyncio
    from services.summary_resolver import resolve_tenant_summary, resolve_user_summary

    tenant_summary, actor_summary = await asyncio.gather(
        resolve_tenant_summary(log.tenant_id),
        resolve_user_summary(log.actor_id),
    )
    data = log.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_summary
    data["actor_summary"] = actor_summary
    return AuditLogWithSummaryOut(**data)


async def retrieve_audit_logs_with_summary(
    filter_dict: Dict[str, Any],
    start: int = 0,
    stop: int = 100,
) -> List[AuditLogWithSummaryOut]:
    """Retrieve audit logs with actor + tenant summaries embedded."""
    import asyncio

    logs = await get_audit_logs(filter_dict, start=start, stop=stop)
    return list(await asyncio.gather(*[_enrich_audit_log(log) for log in logs]))
