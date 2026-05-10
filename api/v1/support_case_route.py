"""Tenant-facing support-case routes.

Any of the 6 tenant roles can open and participate in a case. All
mutations go through the queued write pipeline and return
``202 Accepted + { id, job_id, status: "queued" }``. GETs run inline —
only the page-1 list query is precomputed.

Note: this is NOT ``incident_route.py`` — that route serves the NDPC
breach-tracking log. "Support cases" are platform-level support threads.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, List

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, status

from core.database import db
from core.errors import AppException, ErrorCode
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from core.storage.manager import DocumentStorageManager
from core.storage.types import DocumentMetadata
from repositories.support_case_repo import count_open_cases_for_tenant
from schemas.support_case_schema import (
    SupportCaseAttachmentIntentRequest,
    SupportCaseAttachmentIntentResponse,
    SupportCaseMessageRequest,
    SupportCaseOpenRequest,
    SupportCaseTransitionRequest,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.support_case_service import (
    MAX_OPEN_CASES_PER_TENANT,
    retrieve_messages_for_case,
    retrieve_support_case_by_id,
    retrieve_support_cases,
)


_SUPPORT_STATUSES = frozenset(
    {"open", "acknowledged", "in_progress", "awaiting_tenant", "resolved", "closed", "reopened"}
)
_SUPPORT_PRIORITIES = frozenset({"low", "medium", "high", "critical"})


SUPPORT_CASES_TENANT_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "sla_deadline", "priority", "status", "last_updated"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("title", "summary", "case_number"),
    filters={
        "status": FilterDef(name="status", multi=True, allowed_values=_SUPPORT_STATUSES),
        "priority": FilterDef(name="priority", multi=True, allowed_values=_SUPPORT_PRIORITIES),
        "category": FilterDef(name="category"),
        "slaState": FilterDef(
            name="slaState",
            allowed_values=frozenset({"on_track", "at_risk", "breached"}),
            builder=lambda vs: {"sla_state": vs[0]} if len(vs) == 1 else {"sla_state": {"$in": list(vs)}},
        ),
    },
    range_filters={"createdAt": "date_created"},
    facet_fields=frozenset({"status"}),
)


def _is_default_sc_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(SUPPORT_CASES_TENANT_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(SUPPORT_CASES_TENANT_LIST_SPEC.default_limit)


def _map_sc_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _sc_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _SUPPORT_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support-cases", tags=["Support Cases"])

_tenant_roles = verify_system_user_token(
    "super_admin",
    "dept_admin",
    "receptionist",
    "auditor",
    "security_officer",
    "dpo",
)


async def _load_tenant_cases(tenant_id: str) -> list:
    cases = await retrieve_support_cases(tenant_id=tenant_id, start=0, stop=100)
    return [c.model_dump(mode="json", by_alias=True) for c in cases]


@router.post("")
@document_response(
    message="Support case creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a new support case. The tenant is hard-capped at 10 open "
        "cases; a sync pre-check here returns 429 QUOTA_EXCEEDED before "
        "enqueueing to avoid confusing clients with a 202 followed by a "
        "silently-failed writer. The writer re-checks the cap to close the "
        "race where many concurrent POSTs all pass the sync gate."
    ),
    summary="Open a new support case (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
        429: "Tenant has reached the 10 open-case cap",
    },
)
async def open_support_case(
    payload: SupportCaseOpenRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Tenant context required",
        )

    # Fast pre-check — race-safe re-check happens inside the writer.
    current = await count_open_cases_for_tenant(tenant_id)
    if current >= MAX_OPEN_CASES_PER_TENANT:
        raise AppException(
            status_code=429,
            code=ErrorCode.QUOTA_EXCEEDED,
            message=(
                f"Tenant has reached the maximum of "
                f"{MAX_OPEN_CASES_PER_TENANT} open support cases. Resolve or "
                "close existing cases before opening a new one."
            ),
            details={"open_count": current, "cap": MAX_OPEN_CASES_PER_TENANT},
        )

    body = payload.model_dump(exclude_none=True)
    body["tenant_id"] = tenant_id
    body["opened_by"] = principal.user_id
    body["opened_by_role"] = principal.role
    # Enums may still be objects — coerce to plain string values for JSON transport.
    if hasattr(body.get("category"), "value"):
        body["category"] = body["category"].value
    if hasattr(body.get("priority"), "value"):
        body["priority"] = body["priority"].value

    return await enqueue_write(
        writer_key="support_case.create",
        payload=body,
        resource_type="support_case",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Support cases fetched",
    description=(
        "List cases opened by this tenant. Page-1 with no filters is served "
        "from the tenant-scoped precompute cache; other pages / filters hit "
        "Mongo live."
    ),
    summary="List my tenant's support cases",
    include_meta=True,
)
async def list_my_support_cases(
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}
    if _is_default_sc_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="support_cases.list",
            ttl=60,
            loader=lambda: _load_tenant_cases(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: SUPPORT_CASES_TENANT_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": SUPPORT_CASES_TENANT_LIST_SPEC.default_limit,
                "hasMore": len(items) > SUPPORT_CASES_TENANT_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, SUPPORT_CASES_TENANT_LIST_SPEC)
    return await run_list(
        collection=db.support_cases,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_sc_doc,
        facet_runner=_sc_status_facet,
    )


@router.get("/{case_id}")
@document_response(
    message="Support case fetched",
    description=(
        "Return case metadata plus the full message thread. Internal admin "
        "notes are stripped for tenant readers."
    ),
    summary="Retrieve a single support case with its thread",
)
async def get_support_case(
    case_id: str,
    principal: AuthPrincipal = Depends(_tenant_roles),
) -> Any:
    return await retrieve_support_case_by_id(
        case_id,
        tenant_id=principal.tenant_id,
        requester_role=principal.role,
    )


@router.get("/{case_id}/messages")
@document_response(
    message="Support case thread fetched",
    description="Return the message thread for a case. Internal notes are hidden.",
    summary="List messages on a case",
    include_meta=True,
)
async def list_support_case_messages(
    case_id: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 200,
    principal: AuthPrincipal = Depends(_tenant_roles),
) -> Any:
    # Ownership check by loading with tenant scope; 404 if not the tenant's case.
    await retrieve_support_case_by_id(
        case_id,
        tenant_id=principal.tenant_id,
        requester_role=principal.role,
    )
    messages = await retrieve_messages_for_case(
        case_id,
        requester_role=principal.role,
        start=start,
        stop=stop,
    )
    return messages


@router.post("/{case_id}/messages")
@document_response(
    message="Support case message queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Queue a reply on a case thread. Tenants cannot post internal notes.",
    summary="Reply on a support case (async)",
)
async def reply_on_support_case(
    case_id: str,
    payload: SupportCaseMessageRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    # Ownership / existence check. Raises 404 if the case doesn't belong to the tenant.
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )

    body = payload.model_dump(exclude_none=True)
    # Tenants cannot write internal notes — force False.
    body["internal_note"] = False
    body["case_id"] = case_id
    body["author_id"] = principal.user_id
    body["author_role"] = principal.role

    return await enqueue_write(
        writer_key="support_case.message.add",
        payload=body,
        resource_type="support_case_message",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/close")
@document_response(
    message="Support case close queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Close a case from the tenant side. Legal transitions: RESOLVED→CLOSED."
    ),
    summary="Close a support case (async)",
)
async def close_support_case(
    case_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )
    return await enqueue_write(
        writer_key="support_case.transition",
        payload={
            "status": "closed",
            "actor_id": principal.user_id,
            "actor_role": principal.role,
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/reopen")
@document_response(
    message="Support case reopen queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Reopen a resolved case from the tenant side (RESOLVED→REOPENED).",
    summary="Reopen a support case (async)",
)
async def reopen_support_case(
    case_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )
    return await enqueue_write(
        writer_key="support_case.transition",
        payload={
            "status": "reopened",
            "actor_id": principal.user_id,
            "actor_role": principal.role,
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/transition")
@document_response(
    message="Support case transition queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Generic transition endpoint for the tenant side. Only the legal "
        "tenant-actor transitions are accepted by the writer."
    ),
    summary="Transition a support case (async)",
)
async def tenant_transition_support_case(
    case_id: str,
    payload: SupportCaseTransitionRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )
    return await enqueue_write(
        writer_key="support_case.transition",
        payload={
            "status": payload.status.value,
            "actor_id": principal.user_id,
            "actor_role": principal.role,
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@router.post("/{case_id}/attachments/intent")
@document_response(
    message="Upload intent issued",
    description=(
        "Returns a presigned upload target for an attachment. The client "
        "PUTs the file directly, then calls POST /{case_id}/attachments to "
        "register it."
    ),
    summary="Create a presigned upload intent",
)
async def create_attachment_intent(
    case_id: str,
    payload: SupportCaseAttachmentIntentRequest,
    principal: AuthPrincipal = Depends(_tenant_roles),
) -> SupportCaseAttachmentIntentResponse:
    tenant_id = principal.tenant_id or ""
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )
    try:
        storage = DocumentStorageManager.get_instance()
    except Exception:
        raise AppException(
            status_code=503,
            code=ErrorCode.INTERNAL_ERROR,
            message="Document storage is not configured",
        )
    metadata = DocumentMetadata(
        owner_id=principal.user_id,
        file_name=payload.file_name,
        mime_type=payload.mime_type or "application/octet-stream",
        size=payload.size or 0,
        extra={
            "tenant_id": tenant_id,
            "support_case_id": case_id,
        },
    )
    intent = storage.provider.create_upload_intent(metadata)
    return SupportCaseAttachmentIntentResponse(
        upload_url=intent.upload_url,
        object_key=intent.object_key,
        method=intent.method,
        headers=intent.headers or {},
        expires_in=intent.expires_in,
    )


@router.post("/{case_id}/attachments")
@document_response(
    message="Support case attachment queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Register a completed upload. The writer creates a thread entry "
        "carrying the attachment so it appears alongside regular messages."
    ),
    summary="Register an uploaded attachment (async)",
)
async def register_support_case_attachment(
    case_id: str,
    payload: SupportCaseMessageRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    await retrieve_support_case_by_id(
        case_id, tenant_id=tenant_id, requester_role=principal.role
    )
    body = payload.model_dump(exclude_none=True)
    body["internal_note"] = False  # tenants can't post internal notes
    body["case_id"] = case_id
    body["author_id"] = principal.user_id
    body["author_role"] = principal.role
    return await enqueue_write(
        writer_key="support_case.attachment.add",
        payload=body,
        resource_type="support_case_attachment",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


__all__ = ["router"]

# Silence unused-import warnings on re-exported types.
_ = List
