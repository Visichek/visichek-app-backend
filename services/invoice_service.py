from __future__ import annotations

import logging
import time
from datetime import datetime

from bson import ObjectId

from repositories.invoice_repo import (
    create_invoice,
    count_invoices,
    get_invoice,
    get_invoices,
    get_next_invoice_number,
    update_invoice,
)
from schemas.invoice_schema import (
    InvoiceCreate,
    InvoiceLineItem,
    InvoiceOut,
    InvoiceStatus,
    InvoiceUpdate,
)
from services.invoice_pdf_service import generate_invoice_pdf, get_invoice_pdf_url

logger = logging.getLogger(__name__)


async def generate_invoice(
    tenant_id: str,
    subscription_id: str,
    plan_name: str,
    billing_cycle: str,
    currency: str,
    base_price_minor: int,
    discount_minor: int,
    period_start: int,
    period_end: int,
    payment_transaction_id: str | None = None,
    provider: str | None = None,
    applied_discount_descriptions: list[str] | None = None,
) -> InvoiceOut:
    """Generate an invoice for a subscription payment.

    Called after a successful payment or at subscription creation.
    """
    now = int(time.time())
    year = datetime.utcnow().year
    invoice_number = await get_next_invoice_number(year)

    # Build line items
    line_items: list[InvoiceLineItem] = []

    # Plan line
    cycle_label = "Monthly" if billing_cycle == "monthly" else "Yearly"
    line_items.append(
        InvoiceLineItem(
            description=f"{plan_name} Plan - {cycle_label}",
            quantity=1,
            unit_price_minor=base_price_minor,
            total_minor=base_price_minor,
            metadata={"type": "plan", "billing_cycle": billing_cycle},
        )
    )

    # Discount lines (negative amounts)
    if discount_minor > 0 and applied_discount_descriptions:
        for desc in applied_discount_descriptions:
            line_items.append(
                InvoiceLineItem(
                    description=f"Discount: {desc}",
                    quantity=1,
                    unit_price_minor=-discount_minor,
                    total_minor=-discount_minor,
                    metadata={"type": "discount"},
                )
            )

    total_minor = max(base_price_minor - discount_minor, 0)

    invoice_data = InvoiceCreate(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        invoice_number=invoice_number,
        status=InvoiceStatus.PAID if payment_transaction_id else InvoiceStatus.ISSUED,
        billing_cycle=billing_cycle,
        currency=currency,
        subtotal_minor=base_price_minor,
        discount_total_minor=discount_minor,
        tax_minor=0,
        total_minor=total_minor,
        line_items=line_items,
        payment_transaction_id=payment_transaction_id,
        issued_at=now,
        paid_at=now if payment_transaction_id else None,
        period_start=period_start,
        period_end=period_end,
        provider=provider,
    )

    invoice = await create_invoice(invoice_data)
    logger.info(
        "Invoice generated: %s for tenant %s (amount: %s %s)",
        invoice_number,
        tenant_id,
        total_minor,
        currency,
    )

    # Generate PDF asynchronously (fire and forget pattern)
    # If PDF generation fails, the invoice still exists - it's not critical
    try:
        pdf_object_key = await generate_invoice_pdf(invoice)
        if pdf_object_key:
            await update_invoice(
                invoice.id,
                InvoiceUpdate(pdf_object_key=pdf_object_key),
            )
            logger.info(
                "Invoice PDF generated and stored: invoice_id=%s object_key=%s",
                invoice.id,
                pdf_object_key,
            )
    except Exception as e:
        logger.warning(
            "Failed to generate PDF for invoice %s: %s",
            invoice.id,
            str(e),
        )

    return invoice


async def retrieve_invoice_by_id(invoice_id: str, resolve_pdf_url: bool = True) -> InvoiceOut | None:
    if not ObjectId.is_valid(invoice_id):
        return None
    invoice = await get_invoice({"_id": ObjectId(invoice_id)})

    # Resolve presigned PDF URL if available and requested
    if invoice and resolve_pdf_url and invoice.pdf_object_key:
        try:
            pdf_url = await get_invoice_pdf_url(invoice.pdf_object_key)
            if pdf_url:
                invoice.pdf_url = pdf_url
        except Exception as e:
            logger.warning(
                "Failed to resolve PDF URL for invoice %s: %s",
                invoice_id,
                str(e),
            )

    return invoice


async def retrieve_invoices_for_tenant(
    tenant_id: str,
    skip: int = 0,
    limit: int = 20,
    resolve_pdf_urls: bool = False,
) -> tuple[list[InvoiceOut], int]:
    """Return paginated invoices for a tenant with total count."""
    filter_dict = {"tenant_id": tenant_id}
    invoices = await get_invoices(filter_dict, skip=skip, limit=limit)
    total = await count_invoices(filter_dict)

    # Resolve presigned PDF URLs if requested
    if resolve_pdf_urls:
        for invoice in invoices:
            if invoice.pdf_object_key:
                try:
                    pdf_url = await get_invoice_pdf_url(invoice.pdf_object_key)
                    if pdf_url:
                        invoice.pdf_url = pdf_url
                except Exception as e:
                    logger.warning(
                        "Failed to resolve PDF URL for invoice %s: %s",
                        invoice.id,
                        str(e),
                    )

    return invoices, total


async def retrieve_all_invoices(
    skip: int = 0,
    limit: int = 20,
    tenant_id: str | None = None,
    status: str | None = None,
    resolve_pdf_urls: bool = False,
) -> tuple[list[InvoiceOut], int]:
    """Return paginated invoices for admin view with optional filters."""
    filter_dict: dict = {}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    if status:
        filter_dict["status"] = status
    invoices = await get_invoices(filter_dict, skip=skip, limit=limit)
    total = await count_invoices(filter_dict)

    # Resolve presigned PDF URLs if requested
    if resolve_pdf_urls:
        for invoice in invoices:
            if invoice.pdf_object_key:
                try:
                    pdf_url = await get_invoice_pdf_url(invoice.pdf_object_key)
                    if pdf_url:
                        invoice.pdf_url = pdf_url
                except Exception as e:
                    logger.warning(
                        "Failed to resolve PDF URL for invoice %s: %s",
                        invoice.id,
                        str(e),
                    )

    return invoices, total


async def mark_invoice_paid(
    invoice_id: str,
    payment_transaction_id: str,
) -> InvoiceOut | None:
    """Update an issued invoice to paid status."""
    return await update_invoice(
        invoice_id,
        InvoiceUpdate(
            status=InvoiceStatus.PAID,
            payment_transaction_id=payment_transaction_id,
            paid_at=int(time.time()),
        ),
    )


async def void_invoice(invoice_id: str) -> InvoiceOut | None:
    """Void an invoice (e.g. on subscription cancellation before payment)."""
    return await update_invoice(
        invoice_id,
        InvoiceUpdate(status=InvoiceStatus.VOID),
    )
