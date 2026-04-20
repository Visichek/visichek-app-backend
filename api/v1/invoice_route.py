from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from core.response_envelope import document_response
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.invoice_service import (
    retrieve_all_invoices_with_summary,
    retrieve_invoice_by_id,
    retrieve_invoice_by_id_with_summary,
    retrieve_invoices_for_tenant_with_summary,
)
from services.invoice_pdf_service import get_invoice_pdf_url
from core.errors import resource_not_found

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.get("/tenant/{tenant_id}")
@document_response(
    message="Tenant invoices retrieved",
    include_meta=True,
)
async def list_tenant_invoices(
    tenant_id: str,
    start: int = Query(default=0, ge=0),
    stop: int = Query(default=20, ge=1, le=100),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """List invoices — first page served from the per-tenant precompute cache."""
    from core.queue.precompute import PrecomputeScope, get_or_compute

    if start == 0 and stop == 20:
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="invoices.for_tenant",
            ttl=60,
            loader=lambda: _load_invoices_for_tenant(tenant_id),
        )
        return {
            "items": cached.get("items", []),
            "meta": {"total": cached.get("total", 0), "start": start, "stop": stop},
        }

    invoices, total = await retrieve_invoices_for_tenant_with_summary(
        tenant_id=tenant_id,
        skip=start,
        limit=stop,
    )
    return {"items": invoices, "meta": {"total": total, "start": start, "stop": stop}}


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


@router.get("/admin")
@document_response(
    message="All invoices retrieved",
    include_meta=True,
)
async def list_all_invoices(
    start: int = Query(default=0, ge=0),
    stop: int = Query(default=20, ge=1, le=100),
    tenant_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    """List all invoices — unfiltered first page served from the global precompute cache."""
    from core.queue.precompute import PrecomputeScope, get_or_compute

    if start == 0 and stop == 20 and not tenant_id and not status_filter:
        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="invoices.admin_list",
            ttl=120,
            loader=_load_all_invoices,
        )
        return {
            "items": cached.get("items", []),
            "meta": {"total": cached.get("total", 0), "start": start, "stop": stop},
        }

    invoices, total = await retrieve_all_invoices_with_summary(
        skip=start,
        limit=stop,
        tenant_id=tenant_id,
        status=status_filter,
    )
    return {"items": invoices, "meta": {"total": total, "start": start, "stop": stop}}


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


@router.get("/{invoice_id}")
@document_response(message="Invoice retrieved")
async def get_invoice_detail(
    invoice_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Get a specific invoice by ID, enriched with tenant + subscription summaries."""
    return await retrieve_invoice_by_id_with_summary(invoice_id)


@router.get("/{invoice_id}/pdf")
@document_response(message="Invoice PDF URL generated")
async def get_invoice_pdf(
    invoice_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> dict:
    """Get presigned download URL for invoice PDF.

    Returns a dict with 'pdf_url' key containing the presigned download link.
    """
    invoice = await retrieve_invoice_by_id(invoice_id, resolve_pdf_url=False)

    if not invoice:
        raise resource_not_found(resource="Invoice", resource_id=invoice_id)

    if not invoice.pdf_object_key:
        raise resource_not_found(
            resource="Invoice PDF",
            resource_id=invoice_id,
        )

    pdf_url = await get_invoice_pdf_url(invoice.pdf_object_key)

    if not pdf_url:
        raise resource_not_found(
            resource="Invoice PDF URL",
            resource_id=invoice_id,
        )

    return {"pdf_url": pdf_url}
