"""Test-notification email template (Issue 6).

Mounted at key ``notification_test``. Sent by
``POST /v1/notifications/test`` so admins / tenant users can confirm
the email pipeline is wired without waiting for a real event to fire.

Keeps copy short and deliberately recognisable so an inbox audit can
distinguish test sends from real notifications. Presentation is
delegated entirely to ``_shell`` — only content and logic live here.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "notification_test"
SUBJECT = "VisiChek email diagnostics — test message"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    """Format-safe getter — guards against ``None`` slipping into the template."""
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    triggered_at = _safe(context, "triggered_at", "")
    from_address = _safe(context, "from_address", "")

    triggered_block = ui.muted(f"Triggered at {triggered_at}.") if triggered_at else ""
    from_block = ui.muted(f"Sent from {from_address}.") if from_address else ""

    content = (
        ui.eyebrow("Email diagnostics")
        + ui.heading(f"Hello {recipient_name},")
        + ui.paragraph(f"This is a test email from <strong>{platform_name}</strong>.")
        + ui.paragraph(
            "You’re seeing it because someone (probably you) clicked "
            "<em>Send test email</em> in the notification settings page. "
            "If this reached your inbox the email pipeline is configured "
            "correctly — no further action is needed."
        )
        + triggered_block
        + from_block
    )

    return ui.page(
        content,
        preheader="VisiChek email diagnostics — test message",
    )


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    triggered_at = _safe(context, "triggered_at", "")
    from_address = _safe(context, "from_address", "")

    lines = [
        f"Hello {recipient_name},",
        "",
        f"This is a test email from {platform_name}.",
        "",
        "You're seeing it because someone (probably you) clicked "
        "'Send test email' in the notification settings page. If this "
        "reached your inbox the email pipeline is configured correctly "
        "— no further action is needed.",
    ]
    if triggered_at:
        lines.extend(["", f"Triggered at {triggered_at}."])
    if from_address:
        lines.append(f"Sent from {from_address}.")
    lines.extend(ui.text_signoff())
    return "\n".join(lines)
