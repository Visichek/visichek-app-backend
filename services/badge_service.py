from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_badge_pdf(
    visitor_name: str,
    company: str | None,
    host_department: str | None,
    date_str: str,
    time_in_str: str,
    qr_data: str,
    badge_format: str = "A7",
    visitor_photo_bytes: bytes | None = None,
) -> bytes:
    """Generate a printable visitor badge as PDF bytes.

    Args:
        badge_format: "A6" (105x148mm) or "A7" (74x105mm)
    """
    try:
        from reportlab.lib.pagesizes import A6, A7
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError:
        raise RuntimeError("reportlab is required for badge generation. Install with: pip install reportlab")

    page_size = A7 if badge_format == "A7" else A6
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    margin = 5 * mm

    # Title
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, height - margin - 10, "VISITOR BADGE")

    # QR Code
    try:
        import qrcode
        from PIL import Image as PILImage
        from reportlab.lib.utils import ImageReader

        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)
        qr_size = 30 * mm
        c.drawImage(
            ImageReader(qr_buffer),
            width - margin - qr_size,
            height - margin - 15 - qr_size,
            qr_size,
            qr_size,
        )
    except ImportError:
        logger.warning("qrcode/Pillow not installed; skipping QR code on badge")

    # Visitor photo
    if visitor_photo_bytes:
        try:
            from reportlab.lib.utils import ImageReader
            photo_buffer = io.BytesIO(visitor_photo_bytes)
            photo_size = 20 * mm
            c.drawImage(
                ImageReader(photo_buffer),
                margin,
                height - margin - 15 - photo_size,
                photo_size,
                photo_size,
                preserveAspectRatio=True,
                mask="auto",
            )
            # Adjust text starting position if photo is rendered
            y = height - margin - 15 - photo_size - 5
        except Exception:
            logger.warning("Failed to render visitor photo on badge")
            y = height - margin - 30
    else:
        y = height - margin - 30

    # Visitor info
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin, y, visitor_name or "N/A")
    y -= 12

    c.setFont("Helvetica", 7)
    if company:
        c.drawString(margin, y, f"Company: {company}")
        y -= 10
    if host_department:
        c.drawString(margin, y, f"Visiting: {host_department}")
        y -= 10
    c.drawString(margin, y, f"Date: {date_str}")
    y -= 10
    c.drawString(margin, y, f"Time In: {time_in_str}")

    c.save()
    return buffer.getvalue()
