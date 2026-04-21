"""GET /v1/jobs/{job_id} — poll queued-write job status.

Every enqueue_write call records a queue_job_log entry keyed by the
celery task_id. This endpoint lets a client that received a
``202 + { id, job_id, status }`` response fetch the current state of
the write.

Auth: any token; the server restricts visibility by matching the job's
``tenant_id`` / ``actor_id`` to the caller's principal.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends

from core.errors import AppException, ErrorCode
from core.response_envelope import document_response
from repositories.queue_job_log_repo import (
    get_job_log_by_id,
    get_job_log_by_task_id,
    list_job_logs_for_tenant,
)
from schemas.queue_job_log_schema import QueueJobLogOut
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.summary_resolver import (
    resolve_admin_summary,
    resolve_appointment_summary,
    resolve_branch_summary,
    resolve_department_summary,
    resolve_invoice_summary,
    resolve_plan_summary,
    resolve_subscription_summary,
    resolve_system_user_summary,
    resolve_tenant_summary,
    resolve_user_summary,
    resolve_visit_session_summary,
    resolve_visitor_profile_summary,
)

router = APIRouter(prefix="/jobs", tags=["Queue Jobs"])


# Map resource_type → (resolver, accepts_id). Resolvers return a *BriefSummary
# model or None; the dispatcher wraps the result as a dict for JSON output.
_RESOURCE_RESOLVERS: dict[str, Callable[[Optional[str]], Awaitable[Any]]] = {
    "tenant": resolve_tenant_summary,
    "plan": resolve_plan_summary,
    "subscription": resolve_subscription_summary,
    "department": resolve_department_summary,
    "branch": resolve_branch_summary,
    "system_user": resolve_system_user_summary,
    "admin": resolve_admin_summary,
    "visitor_profile": resolve_visitor_profile_summary,
    "appointment": resolve_appointment_summary,
    "visit_session": resolve_visit_session_summary,
    "invoice": resolve_invoice_summary,
}


async def _resolve_resource_summary(
    resource_type: Optional[str], resource_id: Optional[str]
) -> Optional[dict[str, Any]]:
    if not resource_type or not resource_id:
        return None
    resolver = _RESOURCE_RESOLVERS.get(resource_type)
    if resolver is None:
        return None
    summary = await resolver(resource_id)
    if summary is None:
        return None
    return summary.model_dump(by_alias=False)


def _actor_user_type(actor_role: Optional[str]) -> Optional[str]:
    if actor_role == "admin":
        return "admin"
    if actor_role in TENANT_USER_ROLES:
        return "system_user"
    return None


async def _enrich_log(log: QueueJobLogOut) -> dict[str, Any]:
    tenant_summary, actor_summary, resource_summary = await asyncio.gather(
        resolve_tenant_summary(log.tenant_id),
        resolve_user_summary(log.actor_id, _actor_user_type(log.actor_role)),
        _resolve_resource_summary(log.resource_type, log.resource_id),
    )
    data = log.model_dump(mode="json", by_alias=False)
    # Never leak the full redacted payload or raw error in the status
    # response — operators can query queue_job_log directly for that.
    data.pop("payload_redacted", None)
    data["tenant_summary"] = (
        tenant_summary.model_dump(by_alias=False) if tenant_summary else None
    )
    data["actor_summary"] = (
        actor_summary.model_dump(by_alias=False) if actor_summary else None
    )
    data["resource_summary"] = resource_summary
    return data


def _can_view(principal: AuthPrincipal, log) -> bool:
    if principal.role == "admin":
        return True
    if log.actor_id and log.actor_id == principal.user_id:
        return True
    if principal.role in TENANT_USER_ROLES and log.tenant_id == principal.tenant_id:
        return True
    return False


@router.get("/{job_id}")
@document_response(
    message="Job status fetched successfully",
    description=(
        "Poll a queued write. ``job_id`` is the celery task id returned in "
        "the ``202 + { job_id }`` response. Tenant-user and application-admin "
        "callers can read; other roles only see their own jobs."
    ),
    summary="Get queued-write status",
    success_example={
        "task_id": "2f4fc73a-3672-4d99-a92c-c42deae170b9",
        "task_key": "db.write:discount.delete",
        "resource_type": "discount",
        "resource_id": "69e6c290a4067e541348144f",
        "tenant_id": None,
        "actor_id": "656f7ac12b9d4f6c9e2b9f7d",
        "actor_role": "admin",
        "request_id": "f4ff96c1-6632-45fd-91af-b02667a221c8",
        "status": "failed",
        "result": None,
        "error": (
            "AppException: 400: {'message': 'Cannot delete an active discount. "
            "Disable it first.', 'code': 'VALIDATION_FAILED', "
            "'details': {'discount_id': '69e6c290a4067e541348144f', "
            "'status': 'active'}}"
        ),
        "id": "69e8030eaf7f2106cdfc671f",
        "date_created": 1776812814,
        "last_updated": 1776812814,
        "tenant_summary": None,
        "actor_summary": {
            "id": "656f7ac12b9d4f6c9e2b9f7d",
            "full_name": "Primary Admin",
            "email": "admin@visichek.app",
            "role": "admin",
            "user_type": "admin",
        },
        "resource_summary": None,
    },
    response_codes={
        401: "Unauthorized",
        403: "Caller cannot view this job",
        404: "Job not found",
    },
    error_examples={
        403: {
            "success": False,
            "message": "Not allowed to view this job",
            "data": {"code": "AUTH_PERMISSION_DENIED", "details": None},
        },
        404: {
            "success": False,
            "message": "Job not found",
            "data": {"code": "RESOURCE_NOT_FOUND", "details": None},
        },
    },
)
async def get_job_status(
    job_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    # ``job_id`` is usually the celery task_id; fall back to document _id
    # so operators with the queue_job_log _id can still query.
    log = await get_job_log_by_task_id(job_id) or await get_job_log_by_id(job_id)
    if not log:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Job not found",
        )
    if not _can_view(principal, log):
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="Not allowed to view this job",
        )
    return await _enrich_log(log)


@router.get("")
@document_response(
    message="Jobs fetched successfully",
    description=(
        "List recent queued-write entries for the caller's tenant. "
        "Application admins see the caller's tenant context only — use "
        "the admin tools for cross-tenant views."
    ),
    summary="List recent queued writes",
    include_meta=True,
    success_example=[
        {
            "task_id": "2f4fc73a-3672-4d99-a92c-c42deae170b9",
            "task_key": "db.write:discount.delete",
            "resource_type": "discount",
            "resource_id": "69e6c290a4067e541348144f",
            "tenant_id": None,
            "actor_id": "656f7ac12b9d4f6c9e2b9f7d",
            "actor_role": "admin",
            "request_id": "f4ff96c1-6632-45fd-91af-b02667a221c8",
            "status": "failed",
            "result": None,
            "error": (
                "AppException: 400: {'message': 'Cannot delete an active "
                "discount. Disable it first.', 'code': 'VALIDATION_FAILED'}"
            ),
            "id": "69e8030eaf7f2106cdfc671f",
            "date_created": 1776812814,
            "last_updated": 1776812814,
            "tenant_summary": None,
            "actor_summary": {
                "id": "656f7ac12b9d4f6c9e2b9f7d",
                "full_name": "Primary Admin",
                "email": "admin@visichek.app",
                "role": "admin",
                "user_type": "admin",
            },
            "resource_summary": None,
        },
        {
            "task_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
            "task_key": "db.write:department.create",
            "resource_type": "department",
            "resource_id": "64f1a2b3c4d5e6f7a8b9c0d2",
            "tenant_id": "tenant-001",
            "actor_id": "super-admin-123",
            "actor_role": "super_admin",
            "request_id": "7e1d2c3b-4a5f-4e6d-8c7b-9a0b1c2d3e4f",
            "status": "succeeded",
            "result": {"id": "64f1a2b3c4d5e6f7a8b9c0d2"},
            "error": None,
            "id": "66342cf9a1b2c3d4e5f6a7b8",
            "date_created": 1713700000,
            "last_updated": 1713700002,
            "tenant_summary": {
                "id": "tenant-001",
                "company_name": "Acme Corp",
                "is_active": True,
                "country_of_hosting": "US",
            },
            "actor_summary": {
                "id": "super-admin-123",
                "full_name": "Jane Admin",
                "email": "jane@acme.example",
                "role": "super_admin",
                "user_type": "system_user",
            },
            "resource_summary": {
                "id": "64f1a2b3c4d5e6f7a8b9c0d2",
                "name": "Engineering",
                "code": "ENG",
                "is_active": True,
            },
        },
    ],
    response_codes={
        401: "Unauthorized",
    },
)
async def list_recent_jobs(
    start: int = 0,
    stop: int = 50,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return []
    logs = await list_job_logs_for_tenant(tenant_id, start=start, stop=stop)
    return list(await asyncio.gather(*[_enrich_log(log) for log in logs]))
