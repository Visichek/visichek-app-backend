"""Application-admin facing support-case routes.

Application admins see ALL cases across tenants. Internal notes and admin
filters (assigned_admin_id, support_tier, tenant_id) are available here.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Optional, Sequence

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Query, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.errors import AppException, ErrorCode
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from core.storage.manager import DocumentStorageManager
from repositories.support_case_repo import get_support_case_by_id
from schemas.admin_schema import AdminOut
from schemas.support_case_schema import (
    SupportCaseAssignRequest,
    SupportCaseAttachmentIntentRequest,
    SupportCaseAttachmentIntentResponse,
    SupportCaseMessageRequest,
    SupportCaseTransitionRequest,
)
from security.account_status_check import (
    check_admin_account_status_and_permissions,
)
from services.notification_service import (
    extract_resource_ids,
    schedule_resource_read_receipt,
)
from services.support_case_service import (
    enrich_support_case_dicts,
    retrieve_cases_approaching_sla,
    retrieve_messages_for_case,
    retrieve_support_case_by_id,
    retrieve_support_cases,
)


_SUPPORT_STATUSES = frozenset(
    {
        "open",
        "acknowledged",
        "in_progress",
        "awaiting_tenant",
        "resolved",
        "closed",
        "reopened",
    }
)
_SUPPORT_PRIORITIES = frozenset({"low", "medium", "high", "critical"})

# SLA state is derived, not persisted: nothing ever writes ``sla_state`` to
# the collection, so the old ``{"sla_state": ...}`` builder always matched
# zero rows. It is a window over ``sla_due_at`` for still-active cases;
# active-status set mirrors repositories/support_case_repo.py
# list_sla_breached_cases, at-risk = due within 24h.
_SC_SLA_ACTIVE_STATUSES = ["open", "acknowledged", "in_progress", "reopened"]
_SC_SLA_AT_RISK_WINDOW_SECONDS = 86400


def _sc_sla_state_builder(vs: Sequence[str]) -> dict[str, Any]:
    now = int(time.time())
    soon = now + _SC_SLA_AT_RISK_WINDOW_SECONDS
    clauses = []
    for state in vs:
        if state == "breached":
            due: dict[str, Any] = {"$lte": now}
        elif state == "at_risk":
            due = {"$gt": now, "$lte": soon}
        else:  # on_track
            due = {"$gt": soon}
        clauses.append(
            {"status": {"$in": _SC_SLA_ACTIVE_STATUSES}, "sla_due_at": due}
        )
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


SUPPORT_CASES_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "sla_due_at", "priority", "status", "last_updated"}
    ),
    default_sort=(("date_created", -1),),
    # The support_cases collection stores the user-facing text on `subject`
    # and `description` (see SupportCaseBase) — not title/summary/case_number,
    # which never existed on these docs and made `q` match nothing.
    search_fields=("subject", "description"),
    filters={
        "status": FilterDef(
            name="status", multi=True, allowed_values=_SUPPORT_STATUSES
        ),
        "priority": FilterDef(
            name="priority", multi=True, allowed_values=_SUPPORT_PRIORITIES
        ),
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
        "assigneeId": FilterDef(name="assigneeId", mongo_field="assigned_admin_id"),
        "category": FilterDef(name="category"),
        "supportTier": FilterDef(name="supportTier", mongo_field="support_tier"),
        "slaState": FilterDef(
            name="slaState",
            allowed_values=frozenset({"on_track", "at_risk", "breached"}),
            builder=_sc_sla_state_builder,
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
    limit_raw = qp.get("limit", str(SUPPORT_CASES_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(
        SUPPORT_CASES_LIST_SPEC.default_limit
    )


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

router = APIRouter(prefix="/admins/support-cases", tags=["Application Admin Support"])


async def _load_admin_cases(_: str) -> list:
    cases = await retrieve_support_cases(start=0, stop=100)
    return [c.model_dump(mode="json", by_alias=True) for c in cases]


@router.get("")
@document_response(
    message="Support cases fetched",
    description=(
        "List every support case in the system. Page-1 with no filters is "
        "served from the global precompute cache."
    ),
    summary="List all support cases (admin)",
    include_meta=True,
)
async def admin_list_support_cases(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_sc_listing(request):
        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="support_cases.admin_list",
            ttl=60,
            loader=lambda: _load_admin_cases(""),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: SUPPORT_CASES_LIST_SPEC.default_limit]
        result = {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": SUPPORT_CASES_LIST_SPEC.default_limit,
                "hasMore": len(items) > SUPPORT_CASES_LIST_SPEC.default_limit,
            },
        }
        _auto_read_admin_support_cases(admin, result)
        return result
    query = parse_list_query(request, SUPPORT_CASES_LIST_SPEC)
    result = await run_list(
        collection=db.support_cases,
        query=query,
        map_doc=_map_sc_doc,
        facet_runner=_sc_status_facet,
    )
    # run_list returns raw docs; enrich them with the same tenant/opener/
    # assignee summaries the cached default page carries so the response
    # shape is identical whether or not filters are applied.
    raw_items = result.get("items", [])
    items_list: list[dict[str, Any]] = (
        list(raw_items) if isinstance(raw_items, list) else []
    )
    result["items"] = await enrich_support_case_dicts(items_list)
    _auto_read_admin_support_cases(admin, result)
    return result


def _auto_read_admin_support_cases(admin: AdminOut, result: Any) -> None:
    """Auto-mark this admin's support-case notifications read for ids read."""
    schedule_resource_read_receipt(
        user_id=admin.id or "",
        user_role="admin",
        resource_type="support_case",
        resource_ids=extract_resource_ids(result),
    )


@router.get("/approaching-sla")
@document_response(
    message="Support cases approaching SLA fetched",
    description="Active cases whose SLA deadline elapses within 24 hours.",
    summary="List SLA-at-risk cases",
    include_meta=True,
)
async def admin_approaching_sla(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_cases_approaching_sla(start=start, stop=stop)


@router.get("/{case_id}")
@document_response(
    message="Support case fetched",
    description="Full case detail including internal notes.",
    summary="Retrieve a support case (admin)",
)
async def admin_get_support_case(
    case_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    # Safe to share cache here — admin always sees the unredacted view.
    # Tenant-scope reads use a different entity_type so internal notes
    # never leak across roles.
    result = await get_or_compute_entity(
        entity_type="support_case_admin",
        entity_id=case_id,
        loader=lambda: retrieve_support_case_by_id(
            case_id, tenant_id=None, requester_role="admin"
        ),
    )
    schedule_resource_read_receipt(
        user_id=admin.id or "",
        user_role="admin",
        resource_type="support_case",
        resource_ids=[case_id],
    )
    return result


@router.get("/{case_id}/messages")
@document_response(
    message="Support case thread fetched",
    description="Full message thread including internal notes.",
    summary="List messages on a case (admin)",
    include_meta=True,
)
async def admin_list_messages(
    case_id: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 200,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_messages_for_case(
        case_id, requester_role="admin", start=start, stop=stop
    )


@router.post("/{case_id}/messages")
@document_response(
    message="Admin support case message queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Reply on a case as an admin. Admins can mark the message as an "
        "internal note, in which case tenants never see it and no tenant "
        "email goes out."
    ),
    summary="Reply on a support case (admin, async)",
)
async def admin_reply_on_support_case(
    case_id: str,
    payload: SupportCaseMessageRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    # Validate the case exists.
    await retrieve_support_case_by_id(case_id, tenant_id=None, requester_role="admin")
    body = payload.model_dump(exclude_none=True)
    body["case_id"] = case_id
    body["author_id"] = admin.id or ""
    body["author_role"] = "admin"
    return await enqueue_write(
        writer_key="support_case.message.add",
        payload=body,
        resource_type="support_case_message",
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Bulk endpoints ───────────────────────────────────────────────────


@router.post("/bulk/assign")
@document_response(
    message="Bulk assign queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk assign cases",
)
async def bulk_assign_cases(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    actor_id = admin.id or ""
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/admins/support-cases/bulk/assign",
        body=payload,
    )
    if hit is not None:
        return hit.response
    assignee_id = str(payload.get("assigneeId") or "")[:64]
    if not assignee_id:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="assigneeId is required",
        )
    response = await enqueue_bulk_write(
        writer_key="support_case.bulk_assign",
        ids=payload.get("ids", []),
        resource_type="support_case",
        extras={"assignee_id": assignee_id},
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/admins/support-cases/bulk/assign",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/status")
@document_response(
    message="Bulk status change queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk transition cases",
)
async def bulk_transition_cases(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    actor_id = admin.id or ""
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/admins/support-cases/bulk/status",
        body=payload,
    )
    if hit is not None:
        return hit.response
    target_status = str(payload.get("status") or "")
    if target_status not in _SUPPORT_STATUSES:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="status is required and must be a valid SupportCaseStatus",
            details={"allowed": sorted(_SUPPORT_STATUSES)},
        )
    response = await enqueue_bulk_write(
        writer_key="support_case.bulk_transition",
        ids=payload.get("ids", []),
        resource_type="support_case",
        extras={"status": target_status},
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/admins/support-cases/bulk/status",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/close")
@document_response(
    message="Bulk close queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk close cases",
)
async def bulk_close_cases(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    payload["status"] = "closed"
    return await bulk_transition_cases(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        admin=admin,
    )


@router.post("/{case_id}/assign")
@document_response(
    message="Support case assignment queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Assign (or re-assign) an admin to a case. For PRIORITY-tier "
        "tenants the assigned admin gets an email ping."
    ),
    summary="Assign a case (admin, async)",
)
async def admin_assign_support_case(
    case_id: str,
    payload: SupportCaseAssignRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    await retrieve_support_case_by_id(case_id, tenant_id=None, requester_role="admin")
    return await enqueue_write(
        writer_key="support_case.assign",
        payload={
            "admin_id": payload.admin_id,
            "actor_id": admin.id or "",
            "actor_role": "admin",
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/transition")
@document_response(
    message="Support case transition queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Admin-side state transitions (acknowledged, in_progress, awaiting_tenant, resolved).",
    summary="Transition a case (admin, async)",
)
async def admin_transition_support_case(
    case_id: str,
    payload: SupportCaseTransitionRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    await retrieve_support_case_by_id(case_id, tenant_id=None, requester_role="admin")
    return await enqueue_write(
        writer_key="support_case.transition",
        payload={
            "status": payload.status.value,
            "actor_id": admin.id or "",
            "actor_role": "admin",
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Attachments (admin side)
# ---------------------------------------------------------------------------


@router.post("/{case_id}/attachments/intent")
@document_response(
    message="Upload intent issued",
    description=(
        "Returns a presigned upload target for an attachment. The admin "
        "PUTs the file directly, then calls POST /{case_id}/attachments to "
        "register it."
    ),
    summary="Create a presigned upload intent (admin)",
)
async def admin_create_attachment_intent(
    case_id: str,
    payload: SupportCaseAttachmentIntentRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> SupportCaseAttachmentIntentResponse:
    case = await get_support_case_by_id(case_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    try:
        storage = DocumentStorageManager.get_instance()
    except Exception:
        raise AppException(
            status_code=503,
            code=ErrorCode.INTERNAL_ERROR,
            message="Document storage is not configured",
        )
    from pathlib import Path
    from uuid import uuid4

    mime_type = payload.mime_type or "application/octet-stream"
    extension = Path(payload.file_name).suffix
    object_key = (
        f"support-cases/{case.tenant_id or 'shared'}/{case_id}/{uuid4().hex}{extension}"
    )
    intent = storage.provider.presign_put(object_key=object_key, mime_type=mime_type)
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
        "carrying the attachment. Admins may flag the entry as an internal "
        "note, in which case tenants never see it."
    ),
    summary="Register an uploaded attachment (admin, async)",
)
async def admin_register_support_case_attachment(
    case_id: str,
    payload: SupportCaseMessageRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    case = await get_support_case_by_id(case_id)
    if case is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Support case not found",
        )
    body = payload.model_dump(exclude_none=True)
    body["case_id"] = case_id
    body["author_id"] = admin.id or ""
    body["author_role"] = "admin"
    return await enqueue_write(
        writer_key="support_case.attachment.add",
        payload=body,
        resource_type="support_case_attachment",
        tenant_id=case.tenant_id,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


__all__ = ["router"]
