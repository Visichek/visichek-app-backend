from typing import Annotated
from fastapi import APIRouter, Depends, Query
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.audit_log_repo import get_audit_logs, count_audit_logs

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])
_audit_roles = verify_system_user_token("super_admin", "auditor", "dpo")


@router.get("/")
@document_response(
    message="Audit logs fetched successfully",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "tenant-12345",
            "actor_id": "550e8400-e29b-41d4-a716-446655440000",
            "actor_name_snapshot": "John Admin",
            "user_session_id": "sess_abc123def456",
            "action": "CREATE_DEPARTMENT",
            "target_entity": "department",
            "target_id": "dept-9876543210",
            "ip": "192.168.1.100",
            "device_signature": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "reason": "New department added for compliance team",
            "occurred_at": 1712544800,
        }
    ],
    description="Retrieve audit logs with optional filtering by actor, action, entity, or date range",
    summary="List audit logs",
    include_meta=True,
    response_codes={
        200: "Audit logs fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or missing token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
    },
)
async def list_audit_logs(
    actor_id: str = None,
    action: str = None,
    target_entity: str = None,
    date_from: int = None,
    date_to: int = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_audit_roles),
):
    filter_dict = {"tenant_id": principal.tenant_id or ""}
    if actor_id:
        filter_dict["actor_id"] = actor_id
    if action:
        filter_dict["action"] = action
    if target_entity:
        filter_dict["target_entity"] = target_entity
    if date_from or date_to:
        time_filter = {}
        if date_from:
            time_filter["$gte"] = date_from
        if date_to:
            time_filter["$lte"] = date_to
        filter_dict["occurred_at"] = time_filter

    logs = await get_audit_logs(filter_dict, start=start, stop=stop)
    total = await count_audit_logs(filter_dict)
    return {"items": logs, "total": total}
