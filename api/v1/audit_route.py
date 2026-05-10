from __future__ import annotations

import io
import time
from typing import Annotated, Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from core.csv_export import csv_response
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from repositories.audit_log_repo import count_audit_logs
from schemas.admin_schema import AdminOut
from security.account_status_check import (
    check_admin_account_status_and_permissions,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.audit_service import retrieve_audit_logs_with_summary
from services.export_service import export_audit_logs_xlsx

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])
_audit_roles = verify_system_user_token("super_admin", "auditor", "dpo")


_AUDIT_OPERATIONS = frozenset({"create", "read", "update", "delete"})


AUDIT_LOG_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"timestamp"}),
    default_sort=(("timestamp", -1),),
    search_fields=("action", "details_summary"),
    filters={
        "actorUserId": FilterDef(name="actorUserId", mongo_field="actor_id"),
        "actorRole": FilterDef(name="actorRole", mongo_field="actor_role"),
        "operation": FilterDef(name="operation", allowed_values=_AUDIT_OPERATIONS),
        "resourceType": FilterDef(name="resourceType", mongo_field="resource_type"),
        "resourceId": FilterDef(name="resourceId", mongo_field="resource_id"),
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
        "action": FilterDef(name="action"),
    },
    range_filters={"timestamp": "timestamp"},
    facet_fields=frozenset(),
    default_limit=50,
)


_DEFAULT_RANGE_SECONDS = 7 * 24 * 60 * 60


def _ensure_default_range(filter_doc: dict[str, Any]) -> dict[str, Any]:
    """Tenant audit listings default to the last 7 days unless the
    caller provides an explicit ``timestampGte``/``timestampLte`` pair.

    Unbounded queries on ``audit_trail`` are slow and have caused page
    timeouts in production. Forcing a window here means the worst case
    is bounded by the index + the 7-day slice.
    """
    if "timestamp" in filter_doc:
        return filter_doc
    now = int(time.time())
    filter_doc["timestamp"] = {"$gte": now - _DEFAULT_RANGE_SECONDS, "$lte": now}
    return filter_doc


def _build_filter(
    base: Dict[str, Any],
    *,
    actor_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    date_from: Optional[int],
    date_to: Optional[int],
) -> Dict[str, Any]:
    filter_dict: Dict[str, Any] = dict(base)
    if actor_id:
        filter_dict["actor_id"] = actor_id
    if action:
        filter_dict["action"] = action
    if resource_type:
        filter_dict["resource_type"] = resource_type
    if resource_id:
        filter_dict["resource_id"] = resource_id
    if date_from or date_to:
        time_filter: Dict[str, Any] = {}
        if date_from:
            time_filter["$gte"] = date_from
        if date_to:
            time_filter["$lte"] = date_to
        filter_dict["timestamp"] = time_filter
    return filter_dict


@router.get("")
@document_response(
    message="Audit logs fetched successfully",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "65a1f4d2c8e4b81b2a1c0c0d",
            "actor_id": "65a1f4d2c8e4b81b2a1c0c0e",
            "actor_role": "super_admin",
            "action": "subscription.cancelled",
            "resource_type": "subscription",
            "resource_id": "65a1f4d2c8e4b81b2a1c0c0f",
            "details": {"reason": "non-payment"},
            "request_id": "req_abc123",
            "timestamp": 1712544800,
            "tenant_summary": {
                "id": "65a1f4d2c8e4b81b2a1c0c0d",
                "company_name": "Acme Corp",
                "is_active": True,
                "country_of_hosting": "US",
            },
            "actor_summary": {
                "id": "65a1f4d2c8e4b81b2a1c0c0e",
                "full_name": "Jane Doe",
                "email": "jane@acme.com",
                "role": "super_admin",
                "user_type": "system_user",
            },
            "resource_summary": {
                "id": "65a1f4d2c8e4b81b2a1c0c0f",
                "status": "cancelled",
                "billing_cycle": "monthly",
                "plan_id": "65a1f4d2c8e4b81b2a1c0c10",
                "current_period_end": 1712544800,
            },
        }
    ],
    description=(
        "Tenant-scoped audit log listing. Each row carries embedded "
        "``tenant_summary``, ``actor_summary`` and (when the resource type "
        "is recognised) ``resource_summary`` so the frontend never needs a "
        "follow-up request to render a row."
    ),
    summary="List audit logs for the current tenant",
    include_meta=True,
    response_codes={
        200: "Audit logs fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or missing token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def list_audit_logs(
    request: Request,
    principal: AuthPrincipal = Depends(_audit_roles),
):
    tenant_id = principal.tenant_id or ""
    qp = request.query_params
    legacy_keys = {"actor_id", "action", "resource_type", "resource_id", "date_from", "date_to", "start", "stop"}
    new_keys = {"actorUserId", "actorRole", "operation", "resourceType", "resourceId", "tenantId", "timestampGte", "timestampLte", "q", "sort", "facets", "skip", "limit"}
    has_new_param = any(k in qp for k in new_keys if qp.get(k))
    has_legacy = any(k in qp for k in legacy_keys if qp.get(k))

    # Fast-path precompute is only used for the default unfiltered view.
    if not has_new_param and not has_legacy and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="audit.recent",
            ttl=60,
            loader=lambda: _load_audit_recent_for_tenant(tenant_id),
        )

    # Legacy compatibility path — keep returning the old shape for clients
    # that haven't migrated yet.
    if has_legacy:
        legacy_filter = _build_filter(
            {"tenant_id": tenant_id} if tenant_id else {},
            actor_id=qp.get("actor_id"),
            action=qp.get("action"),
            resource_type=qp.get("resource_type"),
            resource_id=qp.get("resource_id"),
            date_from=int(qp["date_from"]) if qp.get("date_from") else None,
            date_to=int(qp["date_to"]) if qp.get("date_to") else None,
        )
        start = int(qp.get("start", "0"))
        stop = int(qp.get("stop", "100"))
        logs = await retrieve_audit_logs_with_summary(
            legacy_filter, start=start, stop=stop
        )
        total = await count_audit_logs(legacy_filter)
        return {"items": logs, "total": total}

    query = parse_list_query(request, AUDIT_LOG_LIST_SPEC)
    base_filter: dict[str, Any] = {}
    if tenant_id:
        base_filter["tenant_id"] = tenant_id
    return await run_list(
        collection=db.audit_trail,
        query=query,
        base_filter=base_filter,
        map_doc=_map_audit_doc,
    )


def _map_audit_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("/admin")
@document_response(
    message="Audit logs fetched successfully",
    description=(
        "Application-admin cross-tenant audit log listing. Use the "
        "``tenant_id`` query parameter to scope to a single tenant; omit it "
        "to see platform-wide events including admin actions where "
        "``tenant_id`` is null."
    ),
    summary="List audit logs across all tenants (application admin only)",
    include_meta=True,
    response_codes={
        200: "Audit logs fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_audit_logs_admin(
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),  # noqa: ARG001
):
    unfiltered = not (
        tenant_id
        or actor_id
        or actor_role
        or action
        or resource_type
        or resource_id
        or date_from
        or date_to
    )
    if unfiltered and start == 0 and stop == 100:
        return await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="audit.admin_recent",
            ttl=60,
            loader=_load_audit_recent_admin,
        )

    base: Dict[str, Any] = {}
    if tenant_id:
        base["tenant_id"] = tenant_id
    if actor_role:
        base["actor_role"] = actor_role

    filter_dict = _build_filter(
        base,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
    )
    logs = await retrieve_audit_logs_with_summary(filter_dict, start=start, stop=stop)
    total = await count_audit_logs(filter_dict)
    return {"items": logs, "total": total}


async def _load_audit_recent_for_tenant(tenant_id: str) -> Dict[str, Any]:
    filter_dict = {"tenant_id": tenant_id}
    logs = await retrieve_audit_logs_with_summary(filter_dict, start=0, stop=100)
    total = await count_audit_logs(filter_dict)
    return {
        "items": [log.model_dump(mode="json", by_alias=True) for log in logs],
        "total": total,
    }


async def _load_audit_recent_admin() -> Dict[str, Any]:
    logs = await retrieve_audit_logs_with_summary({}, start=0, stop=100)
    total = await count_audit_logs({})
    return {
        "items": [log.model_dump(mode="json", by_alias=True) for log in logs],
        "total": total,
    }


def _audit_export_filename(prefix: str) -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return f"{prefix}-{stamp}.xlsx"


@router.get("/csv")
async def export_tenant_audit_logs_csv(
    request: Request,
    principal: AuthPrincipal = Depends(_audit_roles),
):
    """Streaming CSV export — same filters as the list endpoint.

    Returned columns are documented in tables.txt; values are escaped
    against CSV-formula injection per OWASP CWE-1236.
    """
    tenant_id = principal.tenant_id or ""
    query = parse_list_query(request, AUDIT_LOG_LIST_SPEC)
    base_filter: dict[str, Any] = {}
    if tenant_id:
        base_filter["tenant_id"] = tenant_id
    filter_doc = query.to_mongo(base_filter)
    _ensure_default_range(filter_doc)
    cursor = db.audit_trail.find(filter_doc).sort(query.mongo_sort()).limit(10000)

    async def _row_iter():
        async for doc in cursor:
            doc["_id"] = str(doc["_id"]) if isinstance(doc.get("_id"), ObjectId) else doc.get("_id")
            yield doc

    columns = [
        "_id",
        "timestamp",
        "actor_id",
        "actor_role",
        "action",
        "resource_type",
        "resource_id",
        "tenant_id",
        "request_id",
    ]
    return csv_response(
        rows=_row_iter(),
        columns=columns,
        filename=f"audit-logs-{tenant_id or 'platform'}",
    )


@router.get("/export")
async def export_tenant_audit_logs(
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    limit: Annotated[int, Query(gt=0, le=50000)] = 10000,
    principal: AuthPrincipal = Depends(_audit_roles),
):
    """Stream the current tenant's audit trail as an XLSX with frozen headers.

    Filters mirror ``GET /v1/audit-logs`` so the same query a user runs in
    the table view downloads as a spreadsheet. Capped at ``limit`` rows
    (default 10 000, max 50 000) — paginate-and-call-again is intentionally
    not supported because XLSX isn't a streaming format."""
    tenant_id = principal.tenant_id or ""
    data = await export_audit_logs_xlsx(
        tenant_id=tenant_id or None,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    filename = _audit_export_filename("audit-logs")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/export")
async def export_admin_audit_logs(
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    limit: Annotated[int, Query(gt=0, le=50000)] = 10000,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    """Cross-tenant audit-trail export for application admins.

    Same shape as the tenant export but scope is the entire platform.
    Pass ``tenant_id`` to scope to a single tenant; omit it to capture
    platform-wide events including admin actions where ``tenant_id`` is
    null."""
    _ = admin  # auth dependency only — the query itself is cross-tenant
    data = await export_audit_logs_xlsx(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    filename = _audit_export_filename("audit-logs-platform")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
