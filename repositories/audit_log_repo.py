"""Append-only audit log repository. NO update or delete functions."""
from core.database import db
from typing import List
from schemas.audit_log_schema import AuditLogCreate, AuditLogOut


async def create_audit_log(log_data: AuditLogCreate) -> AuditLogOut:
    log_dict = log_data.model_dump()
    result = await db.system_audit_logs.insert_one(log_dict)
    result = await db.system_audit_logs.find_one({"_id": result.inserted_id})
    return AuditLogOut(**result)


async def get_audit_logs(filter_dict: dict = {}, start=0, stop=100) -> List[AuditLogOut]:
    cursor = (
        db.system_audit_logs.find(filter_dict)
        .sort("occurred_at", -1)
        .skip(start)
        .limit(stop - start)
    )
    return [AuditLogOut(**doc) async for doc in cursor]


async def count_audit_logs(filter_dict: dict) -> int:
    return await db.system_audit_logs.count_documents(filter_dict)
