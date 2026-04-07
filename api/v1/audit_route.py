from typing import Annotated
from fastapi import APIRouter, Depends, Query
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.audit_log_repo import get_audit_logs, count_audit_logs

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])
_audit_roles = verify_system_user_token("super_admin", "auditor", "dpo")


@router.get("/")
@document_response(message="Audit logs fetched successfully")
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
        filter_dict["timestamp"] = time_filter

    logs = await get_audit_logs(filter_dict, start=start, stop=stop)
    total = await count_audit_logs(filter_dict)
    return {"items": logs, "total": total}
