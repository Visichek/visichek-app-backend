"""DSR 'completed' notification.

Mounted at key ``dsr_completed``. Sent to the requester + linked visitor when
a data subject request is completed (status → completed). Carries the
documented resolution when one was recorded. For an *access* request the
separate ``dsr_access_package`` email delivers the actual data download link;
this message is the workflow confirmation.
"""

from __future__ import annotations

import html
from typing import Any

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

    resolution_block = (
        f"<p style='color:#444;font-size:14px'>{resolution}</p>" if resolution else ""
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;max-width:560px'>"
        f"<h2 style='margin:0 0 8px'>All done, {name}.</h2>"
        f"<p>Your <strong>{request_type}</strong> request to {tenant_name} has "
        "been completed.</p>"
        f"{resolution_block}"
        "<p style='color:#666;font-size:13px'>If you expected a data download "
        "and didn't receive a separate email with a secure link, reply and "
        f"{tenant_name} will resend it.</p>"
        "<hr style='border:none;border-top:1px solid #e5e7eb;margin:20px 0' />"
        f"<p style='color:#888;font-size:12px'>Sent by {tenant_name} via VisiChek.</p>"
        "</div>"
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
            f"Sent by {tenant_name} via VisiChek.",
        ]
    )
    return "\n".join(lines)
