from __future__ import annotations

import logging

from repositories.audit_log_repo import create_audit_log
from schemas.audit_log_schema import AuditLogCreate

logger = logging.getLogger(__name__)


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
