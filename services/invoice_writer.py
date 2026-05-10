"""Queued bulk-write handlers for invoices.

Per-id mutations on invoices remain synchronous (mark_paid / void) on
the existing routes; only the bulk surface goes through the queue
because batching N voids or N PDF downloads benefits from a single
audit row + cache invalidation per call.

Bulk download:
* Worker resolves each invoice, validates the PDF object key, and
  fetches a presigned URL.
* The frontend reads ``result.downloads`` (per-id) from the job log.
* No ZIP is built server-side here — the FE can render N download
  links without us streaming a multi-GB blob through the API. If we
  later need a single ZIP, swap this out for a worker that POSTs to
  a streaming archiver and emits one `downloadUrl`.

Security:
* The bulk_void handler refuses to operate cross-tenant — the route
  layer scopes the actor; the writer additionally drops any invoice
  whose tenant_id doesn't match the actor scope when an
  ``extras.tenant_scope`` is passed (super_admin path).
"""

from __future__ import annotations

import logging
from typing import Any

from core.bulk import run_bulk_handlers
from core.queue.write_pipeline import write_handler
from services.invoice_pdf_service import get_invoice_pdf_url
from services.invoice_service import retrieve_invoice_by_id, void_invoice

logger = logging.getLogger(__name__)


@write_handler(
    "invoice.bulk_void",
    invalidates=[
        "invoices.admin_list",
        "invoices.for_tenant",
    ],
)
async def _invoice_bulk_void(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = extras.get("tenant_scope")

    async def _handle(invoice_id: str) -> dict[str, Any]:
        invoice = await retrieve_invoice_by_id(invoice_id, resolve_pdf_url=False)
        if not invoice:
            raise ValueError("Invoice not found")
        if tenant_scope and invoice.tenant_id != tenant_scope:
            # Cross-tenant attempt — fail loudly so the per-id row
            # surfaces in `failed`.
            raise PermissionError("Invoice belongs to a different tenant")
        result = await void_invoice(invoice_id)
        return {
            "id": result.id if result else invoice_id,
            "status": "void",
        }

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler(
    "invoice.bulk_download",
    invalidates=[],
)
async def _invoice_bulk_download(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a presigned PDF URL per id.

    Output shape (matches `core.bulk.run_bulk_handlers`):
    ``{ succeeded: [ { id, result: { downloadUrl } }, ...], failed: [...] }``
    """
    ids = list(data.get("ids", []))
    extras = data.get("extras", {}) or {}
    tenant_scope = extras.get("tenant_scope")

    async def _handle(invoice_id: str) -> dict[str, Any]:
        invoice = await retrieve_invoice_by_id(invoice_id, resolve_pdf_url=False)
        if not invoice:
            raise ValueError("Invoice not found")
        if tenant_scope and invoice.tenant_id != tenant_scope:
            raise PermissionError("Invoice belongs to a different tenant")
        if not invoice.pdf_object_key:
            raise ValueError("Invoice has no PDF")
        url = await get_invoice_pdf_url(invoice.pdf_object_key)
        if not url:
            raise ValueError("Could not generate signed URL")
        return {"downloadUrl": url}

    return await run_bulk_handlers(ids, _handle, atomic=False)
