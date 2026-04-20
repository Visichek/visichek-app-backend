"""GET /v1/jobs/{job_id} — poll queued-write job status.

Every enqueue_write call records a queue_job_log entry keyed by the
celery task_id. This endpoint lets a client that received a
``202 + { id, job_id, status }`` response fetch the current state of
the write.

Auth: any token; the server restricts visibility by matching the job's
``tenant_id`` / ``actor_id`` to the caller's principal.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.errors import AppException, ErrorCode
from core.response_envelope import document_response
from repositories.queue_job_log_repo import (
    get_job_log_by_id,
    get_job_log_by_task_id,
    list_job_logs_for_tenant,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES

router = APIRouter(prefix="/jobs", tags=["Queue Jobs"])


def _serialise_log(log) -> dict:
    data = log.model_dump(mode="json", by_alias=False)
    # Never leak the full redacted payload or raw error in the status
    # response — operators can query queue_job_log directly for that.
    data.pop("payload_redacted", None)
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
        "id": "66342cf9a1b2c3d4e5f6a7b8",
        "task_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "task_key": "db.write:department.create",
        "resource_type": "department",
        "resource_id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "tenant-001",
        "actor_id": "super-admin-123",
        "actor_role": "super_admin",
        "status": "succeeded",
        "result": {"id": "64f1a2b3c4d5e6f7a8b9c0d2"},
        "error": None,
        "date_created": 1713700000,
        "last_updated": 1713700002,
    },
    response_codes={
        401: "Unauthorized",
        403: "Caller cannot view this job",
        404: "Job not found",
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
    return _serialise_log(log)


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
    return [_serialise_log(log) for log in logs]
