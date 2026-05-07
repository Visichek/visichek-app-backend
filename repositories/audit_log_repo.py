"""Append-only audit-trail repository.

Reads and writes the ``audit_trail`` collection — the same collection that
``services.audit_service.record_audit_event`` populates. Earlier this module
pointed at a ``system_audit_logs`` collection that no live writer used,
which is why the audit-logs endpoint returned an empty page.

NO update or delete functions. Audits are immutable.
"""

from __future__ import annotations

from typing import List

from core.database import db
from schemas.audit_log_schema import AuditLogCreate, AuditLogOut

COLLECTION = "audit_trail"


async def create_audit_log(log_data: AuditLogCreate) -> AuditLogOut:
    log_dict = log_data.model_dump()
    insert_result = await db[COLLECTION].insert_one(log_dict)
    fetched = await db[COLLECTION].find_one({"_id": insert_result.inserted_id})
    assert fetched is not None  # we just inserted it
    return AuditLogOut(**fetched)


async def get_audit_logs(
    filter_dict: dict | None = None, start: int = 0, stop: int = 100
) -> List[AuditLogOut]:
    cursor = (
        db[COLLECTION]
        .find(filter_dict or {})
        .sort("timestamp", -1)
        .skip(start)
        .limit(stop - start)
    )
    return [AuditLogOut(**doc) async for doc in cursor]


async def count_audit_logs(filter_dict: dict | None = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})
