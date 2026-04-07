from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from bson import ObjectId

from core.database import db
from repositories.audit_log_repo import create_audit_log
from schemas.audit_log_schema import AuditLogCreate

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
        await create_audit_log(AuditLogCreate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=actor_name_snapshot,
            action=action,
            target_entity=target_entity,
            target_id=target_id,
            ip=ip,
            device_signature=device_signature,
            reason=reason,
        ))
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}")


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

    Fire-and-forget: logs errors but does not raise exceptions.

    Args:
        actor_id: User ID of the actor (admin or system user)
        actor_role: Role of the actor (admin, super_admin, etc.)
        action: Action name (e.g., "subscription.created", "plan.archived", "tenant.offboarded")
        resource_type: Type of resource affected (e.g., "subscription", "plan", "tenant")
        resource_id: ID of the affected resource
        tenant_id: Tenant ID if applicable
        details: Additional context as a dict
        request_id: Request ID for correlation

    Returns:
        Inserted document ID, or None if insertion failed
    """
    try:
        now = int(time.time())
        event_doc = {
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "tenant_id": tenant_id,
            "details": details or {},
            "timestamp": now,
            "request_id": request_id,
        }
        result = await db[AUDIT_TRAIL_COLLECTION].insert_one(event_doc)
        return str(result.inserted_id)
    except Exception as e:
        logger.error(
            "Failed to record audit event: action=%s resource=%s error=%s",
            action,
            resource_id,
            str(e),
        )
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
