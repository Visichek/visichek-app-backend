from __future__ import annotations

import io
import logging
from datetime import datetime
from decimal import Decimal

from core.storage.manager import DocumentStorageManager
from core.storage.types import DocumentMetadata
from schemas.invoice_schema import InvoiceOut

logger = logging.getLogger(__name__)


def _generate_pdf_bytes(invoice: InvoiceOut) -> bytes:
    """Generate PDF bytes from invoice data using reportlab."""
    try:
        from reportlab.lib.pagesizes import letter  # type: ignore[import-untyped]
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore[import-untyped]
        from reportlab.lib.units import inch  # type: ignore[import-untyped]
        from reportlab.lib.colors import HexColor, grey  # type: ignore[import-untyped]
        from reportlab.platypus import (  # type: ignore[import-untyped]
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            PageBreak,  # noqa: F401
        )
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER  # type: ignore[import-untyped]
    except ImportError as e:
        logger.error("reportlab is required for PDF generation: %s", str(e))
        raise

    # Create PDF buffer
    pdf_buffer = io.BytesIO()

    # Create document
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    # Container for elements to draw
    elements = []

    # Get base styles
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontSize=28,
        textColor=HexColor("#1a1a1a"),
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    style_label = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=10,
        textColor=grey,
        spaceAfter=2,
    )
    style_value = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        textColor=HexColor("#1a1a1a"),
        spaceAfter=2,
    )

    # --- Header Section ---
    header_data = [
        [Paragraph("INVOICE", style_title), ""],
        [Paragraph("<b>Invoice Number</b>", style_label), Paragraph(invoice.invoice_number, style_value)],
        [
            Paragraph("<b>Invoice Date</b>", style_label),
            Paragraph(
                datetime.utcfromtimestamp(invoice.issued_at or 0).strftime("%B %d, %Y"),
                style_value,
            ),
        ],
        [
            Paragraph("<b>Due Date</b>", style_label),
            Paragraph(
                datetime.utcfromtimestamp(invoice.issued_at or 0).strftime("%B %d, %Y"),
                style_value,
            ),
        ],
        [
            Paragraph("<b>Status</b>", style_label),
            Paragraph(invoice.status.upper(), style_value),
        ],
    ]

    header_table = Table(
        header_data,
        colWidths=[3.5 * inch, 2.5 * inch],
        hAlign="LEFT",
    )
    header_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("LINEABOVE", (0, 0), (-1, 0), 2, HexColor("#333333")),
        ])
    )

    elements.append(header_table)
    elements.append(Spacer(1, 0.2 * inch))

    # --- Billing Period ---
    period_start_str = datetime.utcfromtimestamp(invoice.period_start).strftime("%B %d, %Y")
    period_end_str = datetime.utcfromtimestamp(invoice.period_end).strftime("%B %d, %Y")

    period_data = [
        [
            Paragraph("<b>Billing Period</b>", style_label),
            Paragraph(f"{period_start_str} to {period_end_str}", style_value),
        ]
    ]
    period_table = Table(period_data, colWidths=[2 * inch, 4 * inch])
    period_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    elements.append(period_table)
    elements.append(Spacer(1, 0.15 * inch))

    # --- Line Items Table ---
    line_items_data = [
        ["Description", "Quantity", "Unit Price", "Total"],
    ]

    for item in invoice.line_items:
        unit_price_major = Decimal(item.unit_price_minor) / Decimal(100)
        total_major = Decimal(item.total_minor) / Decimal(100)

        line_items_data.append([
            item.description,
            str(item.quantity),
            f"{invoice.currency} {unit_price_major:.2f}",
            f"{invoice.currency} {total_major:.2f}",
        ])

    line_items_table = Table(
        line_items_data,
        colWidths=[2.8 * inch, 0.8 * inch, 1.2 * inch, 1.2 * inch],
    )
    line_items_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f0f0f0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#1a1a1a")),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), HexColor("#f9f9f9")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("TOPPADDING", (0, 1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ])
    )

    elements.append(line_items_table)
    elements.append(Spacer(1, 0.2 * inch))

    # --- Totals Section ---
    subtotal_major = Decimal(invoice.subtotal_minor) / Decimal(100)
    discount_major = Decimal(invoice.discount_total_minor) / Decimal(100)
    total_major = Decimal(invoice.total_minor) / Decimal(100)

    totals_data = [
        [
            Paragraph("<b>Subtotal</b>", style_value),
            Paragraph(f"{invoice.currency} {subtotal_major:.2f}", style_value),
        ],
    ]

    if invoice.discount_total_minor > 0:
        totals_data.append([
            Paragraph("<b>Discount</b>", style_value),
            Paragraph(f"- {invoice.currency} {discount_major:.2f}", style_value),
        ])

    if invoice.tax_minor > 0:
        tax_major = Decimal(invoice.tax_minor) / Decimal(100)
        totals_data.append([
            Paragraph("<b>Tax</b>", style_value),
            Paragraph(f"{invoice.currency} {tax_major:.2f}", style_value),
        ])

    total_style = ParagraphStyle(
        "TotalAmount",
        parent=styles["Normal"],
        fontSize=14,
        textColor=HexColor("#1a1a1a"),
        alignment=TA_RIGHT,
    )

    totals_data.append([
        Paragraph("<b>TOTAL</b>", total_style),
        Paragraph(f"<b>{invoice.currency} {total_major:.2f}</b>", total_style),
    ])

    totals_table = Table(
        totals_data,
        colWidths=[4.5 * inch, 1.5 * inch],
    )
    totals_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (0, -1), 2),
            ("RIGHTPADDING", (1, 0), (1, -1), 2),
            ("LINEABOVE", (0, -1), (-1, -1), 2, HexColor("#333333")),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
        ])
    )

    elements.append(totals_table)
    elements.append(Spacer(1, 0.3 * inch))

    # --- Footer ---
    footer_text = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        textColor=grey,
        alignment=TA_CENTER,
    )

    if invoice.status.lower() == "paid" and invoice.paid_at:
        paid_date = datetime.utcfromtimestamp(invoice.paid_at).strftime("%B %d, %Y")
        elements.append(Paragraph(f"<i>Invoice paid on {paid_date}</i>", footer_text))
    elif invoice.payment_transaction_id:
        elements.append(
            Paragraph(
                f"<i>Transaction ID: {invoice.payment_transaction_id}</i>",
                footer_text,
            )
        )
    else:
        elements.append(
            Paragraph(
                "<i>This is an automated invoice. Thank you for your business.</i>",
                footer_text,
            )
        )

    # Build PDF
    doc.build(elements)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


async def generate_invoice_pdf(invoice: InvoiceOut) -> str | None:
    """Generate a PDF from invoice data and store it via DocumentStorageManager.

    Args:
        invoice: The invoice to generate PDF for

    Returns:
        Storage object key if successful, None if storage not available or error occurred
    """
    try:
        # Generate PDF bytes
        pdf_bytes = _generate_pdf_bytes(invoice)

        # Prepare storage metadata
        file_name = f"invoice_{invoice.invoice_number}.pdf"
        metadata = DocumentMetadata(
            owner_id=invoice.tenant_id,
            file_name=file_name,
            mime_type="application/pdf",
            size=len(pdf_bytes),
            extra={"invoice_id": invoice.id, "invoice_number": invoice.invoice_number},
        )

        # Upload to storage
        manager = DocumentStorageManager.get_instance()

        # For local storage, we need to handle the upload directly
        # For S3, we get a presigned URL and then upload
        provider = manager.provider

        if provider.backend_name == "s3":
            # S3: use presigned URL flow
            intent = provider.create_upload_intent(metadata)
            # Upload the PDF bytes directly
            import boto3  # type: ignore[import-untyped]
            s3_client = boto3.client("s3")
            s3_client.put_object(
                Bucket=getattr(provider, "_bucket", None),
                Key=intent.object_key,
                Body=pdf_bytes,
                ContentType=metadata.mime_type,
            )
            stored = provider.complete_upload(
                object_key=intent.object_key,
                metadata=metadata,
                checksum=None,
            )
            logger.info(
                "Invoice PDF generated and stored in S3: invoice_id=%s object_key=%s size=%d",
                invoice.id,
                stored.object_key,
                stored.size,
            )
            return stored.object_key
        else:
            # Local storage: use provider's save_bytes method
            from uuid import uuid4

            extension = ".pdf"
            object_key = f"{uuid4().hex}{extension}"

            # Save PDF bytes using provider's save_bytes method
            provider.upload_bytes(object_key=object_key, payload=pdf_bytes, mime_type=metadata.mime_type)
            saved_size = len(pdf_bytes)

            stored = provider.complete_upload(
                object_key=object_key,
                metadata=metadata,
                checksum=None,
            )
            logger.info(
                "Invoice PDF generated and stored locally: invoice_id=%s object_key=%s size=%d",
                invoice.id,
                stored.object_key,
                saved_size,
            )
            return stored.object_key

    except ImportError as e:
        logger.warning(
            "Cannot generate invoice PDF: reportlab not available: %s",
            str(e),
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to generate invoice PDF: invoice_id=%s error=%s",
            invoice.id,
            str(e),
            exc_info=True,
        )
        return None


async def get_invoice_pdf_url(object_key: str) -> str | None:
    """Get a presigned download URL for an invoice PDF.

    Args:
        object_key: Storage object key for the PDF

    Returns:
        Presigned download URL or None if storage not available
    """
    try:
        if not object_key:
            logger.warning("Cannot generate PDF URL: empty object_key")
            return None

        manager = DocumentStorageManager.get_instance()
        url = manager.provider.download_url(object_key=object_key, expires_in=900)
        logger.debug("Generated presigned PDF URL for object_key=%s", object_key)
        return url

    except Exception as e:
        logger.error(
            "Failed to generate invoice PDF URL: object_key=%s error=%s",
            object_key,
            str(e),
            exc_info=True,
        )
        return None
