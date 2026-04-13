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
    """List invoices for the authenticated super admin's tenant, enriched with tenant + subscription summaries."""
    invoices, total = await retrieve_invoices_for_tenant_with_summary(
        tenant_id=tenant_id,
        skip=start,
        limit=stop,
    )
    return {"items": invoices, "meta": {"total": total, "start": start, "stop": stop}}


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
    """List all invoices (application admin only), enriched with tenant + subscription summaries."""
    invoices, total = await retrieve_all_invoices_with_summary(
        skip=start,
        limit=stop,
        tenant_id=tenant_id,
        status=status_filter,
    )
    return {"items": invoices, "meta": {"total": total, "start": start, "stop": stop}}


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
