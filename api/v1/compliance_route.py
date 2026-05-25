from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
import io

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.data_processing_register_repo import get_dpr_entries
from repositories.deletion_log_repo import get_deletion_logs
from schemas.data_processing_register_schema import DPRCreate
from services.compliance_service import (
    get_consent_log,
    get_consent_log_count,
    generate_compliance_export,
)

router = APIRouter(prefix="/compliance", tags=["Compliance"])
_compliance_roles = verify_system_user_token("super_admin", "dpo", "auditor")


@router.get("/register")
@document_response(
    message="Data processing register fetched successfully",
    summary="Get data processing register",
    description="Served from the per-tenant precompute cache.",
    include_meta=True,
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
    },
)
async def get_register(
    principal: AuthPrincipal = Depends(_compliance_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return []
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="compliance.register",
        ttl=120,
        loader=lambda: _load_register(tenant_id),
    )


async def _load_register(tenant_id: str) -> List[Any]:
    entries = await get_dpr_entries({"tenant_id": tenant_id})
    return [
        e.model_dump(mode="json", by_alias=True) if hasattr(e, "model_dump") else e
        for e in entries
    ]


@router.post("/register")
@document_response(
    message="Register entry creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Add data processing register entry (async)",
    description="Enqueue a DPR entry creation.",
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid payload",
    },
)
async def add_register_entry(
    dpr_data: DPRCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    payload = dpr_data.model_dump(exclude_none=True)
    # tenant_id is token-derived, never client-supplied.
    payload["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="compliance.register_dpr",
        payload=payload,
        resource_type="data_processing_register",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/deletion-logs")
@document_response(
    message="Deletion logs fetched successfully",
    summary="List deletion logs",
    description="First page served from the per-tenant precompute cache; paginated reads go live.",
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def list_deletion_logs(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="compliance.deletion_logs",
            ttl=120,
            loader=lambda: _load_deletion_logs(tenant_id),
        )
    return await get_deletion_logs({"tenant_id": tenant_id}, start=start, stop=stop)


async def _load_deletion_logs(tenant_id: str) -> List[Any]:
    logs = await get_deletion_logs({"tenant_id": tenant_id}, start=0, stop=100)
    return [
        log.model_dump(mode="json", by_alias=True)
        if hasattr(log, "model_dump")
        else log
        for log in logs
    ]


@router.get("/consent-log")
@document_response(
    message="Consent log retrieved successfully",
    summary="Get consent log for tenant",
    description="Live query — filters are dynamic so precompute doesn't apply.",
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def get_consent_log_endpoint(
    start_date: Annotated[
        Optional[int], Query(description="Unix timestamp for start date filter")
    ] = None,
    end_date: Annotated[
        Optional[int], Query(description="Unix timestamp for end date filter")
    ] = None,
    skip: Annotated[int, Query(ge=0, description="Number of records to skip")] = 0,
    limit: Annotated[
        int, Query(ge=1, le=1000, description="Maximum records to return")
    ] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    if not principal.tenant_id:
        return []
    results = await get_consent_log(
        tenant_id=principal.tenant_id,
        principal=principal,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )
    await get_consent_log_count(
        tenant_id=principal.tenant_id,
        principal=principal,
        start_date=start_date,
        end_date=end_date,
    )
    return results


@router.get("/export")
@document_response(
    message="Compliance export generated",
    description="Streaming ZIP — stays synchronous.",
    summary="Download compliance export package",
)
async def compliance_export(
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    zip_bytes = await generate_compliance_export(tenant_id=principal.tenant_id or "")
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=compliance_export.zip"},
    )
