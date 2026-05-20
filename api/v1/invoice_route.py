from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.errors import resource_not_found
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.invoice_pdf_service import get_invoice_pdf_url
from services.invoice_service import (
    retrieve_all_invoices_with_summary,
    retrieve_invoice_by_id,
    retrieve_invoice_by_id_with_summary,
    retrieve_invoices_for_tenant_with_summary,
)

router = APIRouter(prefix="/invoices", tags=["Invoices"])


_INVOICE_STATUSES = frozenset({"draft", "issued", "paid", "void", "refunded"})


def _invoice_status_builder(values):
    if "all" in values:
        return {}
    if len(values) == 1:
        return {"status": values[0]}
    return {"status": {"$in": list(values)}}


def _coerce_amount(raw):
    return float(raw)


INVOICES_ADMIN_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"issued_at", "amount", "status", "date_created", "invoice_number"}
    ),
    default_sort=(("issued_at", -1),),
    search_fields=("invoice_number",),
    filters={
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=_INVOICE_STATUSES,
            builder=_invoice_status_builder,
        ),
        "amountGte": FilterDef(
            name="amountGte",
            mongo_field="amount",
            coerce=_coerce_amount,
            builder=lambda vs: {"amount": {"$gte": float(vs[0])}},
        ),
        "amountLte": FilterDef(
            name="amountLte",
            mongo_field="amount",
            coerce=_coerce_amount,
            builder=lambda vs: {"amount": {"$lte": float(vs[0])}},
        ),
    },
    range_filters={"issuedAt": "issued_at"},
    facet_fields=frozenset({"status"}),
)


INVOICES_TENANT_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"issued_at", "amount", "status", "date_created", "invoice_number"}
    ),
    default_sort=(("issued_at", -1),),
    search_fields=("invoice_number",),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=_INVOICE_STATUSES,
            builder=_invoice_status_builder,
        ),
        "amountGte": FilterDef(
            name="amountGte",
            builder=lambda vs: {"amount": {"$gte": float(vs[0])}},
        ),
        "amountLte": FilterDef(
            name="amountLte",
            builder=lambda vs: {"amount": {"$lte": float(vs[0])}},
        ),
    },
    range_filters={"issuedAt": "issued_at"},
    facet_fields=frozenset({"status"}),
)


def _is_default(
    request: Request, spec: ListSpec, *, allow_keys: tuple[str, ...] = ()
) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit", *allow_keys}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(spec.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(spec.default_limit)


def _map_invoice_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _invoice_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for status_value in ("draft", "issued", "paid", "void", "refunded"):
        out[status_value] = await collection.count_documents(
            {**base, "status": status_value}
        )
    out["all"] = sum(out.values())
    return out


# ─── Tenant-scoped invoice list ───────────────────────────────────────


@router.get("/tenant/{tenant_id}")
@document_response(
    message="Tenant invoices retrieved",
    include_meta=True,
)
async def list_tenant_invoices(
    request: Request,
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id != tenant_id:
        # Defence in depth: super_admin token is verified, but the path
        # tenant_id must match the token's tenant_id so a super_admin
        # can't query another tenant's invoices.
        from core.errors import auth_permission_denied

        raise auth_permission_denied(permission_key="invoice.read")

    if _is_default(request, INVOICES_TENANT_LIST_SPEC):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="invoices.for_tenant",
            ttl=60,
            loader=lambda: _load_invoices_for_tenant(tenant_id),
        )
        items = cached.get("items", [])
        total = cached.get("total", 0)
        return {
            "items": items,
            "meta": {
                "total": total,
                "skip": 0,
                "limit": INVOICES_TENANT_LIST_SPEC.default_limit,
                "hasMore": total > len(items),
            },
        }

    query = parse_list_query(request, INVOICES_TENANT_LIST_SPEC)
    return await run_list(
        collection=db.invoices,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_invoice_doc,
        facet_runner=_invoice_status_facet,
    )


async def _load_invoices_for_tenant(tenant_id: str) -> dict:
    invoices, total = await retrieve_invoices_for_tenant_with_summary(
        tenant_id=tenant_id, skip=0, limit=20
    )
    return {
        "items": [
            i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
            for i in invoices
        ],
        "total": total,
    }


# ─── Admin invoice list ───────────────────────────────────────────────


@router.get("/admin")
@document_response(
    message="All invoices retrieved",
    include_meta=True,
)
async def list_all_invoices(
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    if _is_default(request, INVOICES_ADMIN_LIST_SPEC):
        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="invoices.admin_list",
            ttl=120,
            loader=_load_all_invoices,
        )
        items = cached.get("items", [])
        total = cached.get("total", 0)
        return {
            "items": items,
            "meta": {
                "total": total,
                "skip": 0,
                "limit": INVOICES_ADMIN_LIST_SPEC.default_limit,
                "hasMore": total > len(items),
            },
        }

    query = parse_list_query(request, INVOICES_ADMIN_LIST_SPEC)
    return await run_list(
        collection=db.invoices,
        query=query,
        map_doc=_map_invoice_doc,
        facet_runner=_invoice_status_facet,
    )


async def _load_all_invoices() -> dict:
    invoices, total = await retrieve_all_invoices_with_summary(
        skip=0, limit=20, tenant_id=None, status=None
    )
    return {
        "items": [
            i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
            for i in invoices
        ],
        "total": total,
    }


# ─── Bulk endpoints ───────────────────────────────────────────────────


@router.post("/bulk/download")
@document_response(
    message="Bulk invoice download queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a job that bundles selected invoice PDFs into a single "
        "ZIP. Poll GET /v1/jobs/{job_id} for `result.downloadUrl` and "
        "`result.expiresAt`. Worker enforces tenant scoping per id."
    ),
    summary="Bulk download invoice PDFs (admin)",
)
async def bulk_download_invoices(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/invoices/bulk/download",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="invoice.bulk_download",
        ids=payload.get("ids", []),
        resource_type="invoice",
        atomic=False,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/invoices/bulk/download",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/void")
@document_response(
    message="Bulk invoice void queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk void invoices (admin)",
)
async def bulk_void_invoices(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/invoices/bulk/void",
        body=payload,
    )
    if hit is not None:
        return hit.response
    reason = str(payload.get("reason") or "")[:500]
    response = await enqueue_bulk_write(
        writer_key="invoice.bulk_void",
        ids=payload.get("ids", []),
        resource_type="invoice",
        extras={"reason": reason},
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/invoices/bulk/void",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


# Tenant-scoped bulk download (super_admin)
@router.post("/tenant/{tenant_id}/bulk/download")
@document_response(
    message="Bulk invoice download queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk download tenant invoice PDFs",
)
async def bulk_download_tenant_invoices(
    request: Request,
    tenant_id: str,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id != tenant_id:
        from core.errors import auth_permission_denied

        raise auth_permission_denied(permission_key="invoice.read")
    scope = actor_scope(principal.user_id, principal.role)
    route_label = f"POST /v1/invoices/tenant/{tenant_id}/bulk/download"
    hit = check_idempotency(
        key=idempotency_key, scope=scope, route=route_label, body=payload
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="invoice.bulk_download",
        ids=payload.get("ids", []),
        resource_type="invoice",
        extras={"tenant_scope": tenant_id},
        atomic=False,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route=route_label,
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


# ─── Single invoice GET / PDF (unchanged contracts) ──────────────────


@router.get("/{invoice_id}")
@document_response(message="Invoice retrieved")
async def get_invoice_detail(
    invoice_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await get_or_compute_entity(
        entity_type="invoice",
        entity_id=invoice_id,
        loader=lambda: retrieve_invoice_by_id_with_summary(invoice_id),
    )


@router.get("/{invoice_id}/pdf")
@document_response(message="Invoice PDF URL generated")
async def get_invoice_pdf(
    invoice_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> dict:
    invoice = await retrieve_invoice_by_id(invoice_id, resolve_pdf_url=False)
    if not invoice:
        raise resource_not_found(resource="Invoice", resource_id=invoice_id)
    if invoice.tenant_id != principal.tenant_id:
        # Block cross-tenant access — invoice ids are not enumerable but
        # we check anyway because PDF URLs are valuable.
        from core.errors import auth_permission_denied

        raise auth_permission_denied(permission_key="invoice.read")
    if not invoice.pdf_object_key:
        raise resource_not_found(resource="Invoice PDF", resource_id=invoice_id)
    pdf_url = await get_invoice_pdf_url(invoice.pdf_object_key)
    if not pdf_url:
        raise resource_not_found(resource="Invoice PDF URL", resource_id=invoice_id)
    return {"pdf_url": pdf_url}
