"""Test-notification email template (Issue 6).

Mounted at key ``notification_test``. Sent by
``POST /v1/notifications/test`` so admins / tenant users can confirm
the email pipeline is wired without waiting for a real event to fire.

Keeps copy short and deliberately recognisable so an inbox audit can
distinguish test sends from real notifications.
"""

from __future__ import annotations

from typing import Any

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

    triggered_block = (
        f"<p style='color:#666;font-size:12px'>Triggered at {triggered_at}.</p>"
        if triggered_at
        else ""
    )
    from_block = (
        f"<p style='color:#666;font-size:12px'>Sent from {from_address}.</p>"
        if from_address
        else ""
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;max-width:560px'>"
        f"<h2 style='margin:0 0 12px'>Hello {recipient_name},</h2>"
        f"<p>This is a test email from <strong>{platform_name}</strong>.</p>"
        "<p>You're seeing it because someone (probably you) clicked "
        "<em>Send test email</em> in the notification settings page. "
        "If this reached your inbox the email pipeline is configured "
        "correctly — no further action is needed.</p>"
        f"{triggered_block}"
        f"{from_block}"
        "</div>"
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
    return "\n".join(lines)
