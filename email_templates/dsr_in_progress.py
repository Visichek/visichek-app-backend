"""DSR 'now being processed' notification.

Mounted at key ``dsr_in_progress``. Sent to the requester + linked visitor
when a DPO acknowledges a data subject request (status → in_progress), so the
person who raised it knows it's been picked up and is on the clock.

Presentation is delegated to ``_shell``; this module owns only data extraction,
escaping, and content composition.
"""

from __future__ import annotations

import html
from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "dsr_in_progress"
SUBJECT = "{tenant_name}: we're processing your data request"


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

    content = (
        ui.eyebrow("Data request")
        + ui.heading(f"We're on it, {name}.")
        + ui.paragraph(
            f"{tenant_name} has received your <strong>{request_type}</strong> "
            "request and a data protection officer is now processing it. You'll "
            "get another email once it's complete."
        )
        + ui.muted(
            "You're receiving this because a data subject request was raised in "
            f"your name. If that wasn't you, reply to let {tenant_name} know."
        )
    )

    return ui.page(
        content,
        preheader="We're processing your data request",
        footer_note_html=f"Sent by {tenant_name} via VisiChek.",
        brand=ui.build_email_brand(context),
    )


def render_text(context: dict[str, Any]) -> str:
    name = _safe(context, "recipient_name", "there")
    tenant_name = _safe(context, "tenant_name", "VisiChek")
    request_type = _safe(context, "request_type", "data")

    return "\n".join(
        [
            f"We're on it, {name}.",
            "",
            f"{tenant_name} has received your {request_type} request and a data "
            "protection officer is now processing it. You'll get another email "
            "once it's complete.",
            "",
            "If this wasn't you, reply to let us know.",
            "",
            *ui.text_signoff(sender_label=f"Sent by {tenant_name} via VisiChek."),
        ]
    )
