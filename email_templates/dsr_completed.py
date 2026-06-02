"""DSR 'completed' notification.

Mounted at key ``dsr_completed``. Sent to the requester + linked visitor when
a data subject request is completed (status → completed). Carries the
documented resolution when one was recorded. For an *access* request the
separate ``dsr_access_package`` email delivers the actual data download link;
this message is the workflow confirmation that the request has been actioned.
"""

from __future__ import annotations

import html
from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "dsr_completed"
SUBJECT = "{tenant_name}: your data request is complete"


def _safe(context: dict[str, Any], key: str, fallback: str = "") -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    # Escape every interpolated value: recipient_name comes from a visitor's
    # self-entered check-in name, so it's untrusted in an HTML body.
    name = html.escape(_safe(context, "recipient_name", "there"))
    tenant_name = html.escape(_safe(context, "tenant_name", "VisiChek"))
    request_type = html.escape(_safe(context, "request_type", "data"))
    resolution = html.escape(_safe(context, "resolution"))

    resolution_block = ui.paragraph(resolution) if resolution else ""

    content = (
        ui.eyebrow("Data request")
        + ui.heading(f"All done, {name}.")
        + ui.paragraph(
            f"Your <strong>{request_type}</strong> request to {tenant_name} has "
            "been completed."
        )
        + resolution_block
        + ui.muted(
            "If you expected a data download and didn't receive a separate email "
            "with a secure link, reply and "
            f"{tenant_name} will resend it."
        )
    )

    return ui.page(
        content,
        preheader="Your data request is complete",
        footer_note_html=f"Sent by {tenant_name} via VisiChek.",
    )


def render_text(context: dict[str, Any]) -> str:
    name = _safe(context, "recipient_name", "there")
    tenant_name = _safe(context, "tenant_name", "VisiChek")
    request_type = _safe(context, "request_type", "data")
    resolution = _safe(context, "resolution")

    lines = [
        f"All done, {name}.",
        "",
        f"Your {request_type} request to {tenant_name} has been completed.",
    ]
    if resolution:
        lines.extend(["", resolution])
    lines.extend(
        [
            "",
            "If you expected a data download and didn't get a separate email "
            "with a secure link, reply and we'll resend it.",
            "",
        ]
    )
    lines.extend(ui.text_signoff(sender_label=f"Sent by {tenant_name} via VisiChek."))
    return "\n".join(lines)
