"""Password-reset email template.

Sent by ``services/forgot_password_service.send_reset_for_selection``
(step 2 of the forgot-password flow) once an unauthenticated user has
picked which account(s) to reset. Carries a single-use reset *link* —
a button that opens the frontend reset page at
``{APP_BASE_URL}/reset-password?token=…``. The raw token is never
shown to the user as a value to copy: it only ever rides inside the
link. If ``APP_BASE_URL`` is unset the email degrades to showing the
bare URL (which is still empty in that misconfiguration) — operators
must set ``APP_BASE_URL`` for this flow to work end to end.

Presentation is delegated entirely to ``email_templates._shell``; this
module owns only logic and data extraction.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "password_reset"
SUBJECT = "Reset your {platform_name} password"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    reset_url = _safe(context, "reset_url", "")
    ttl_minutes = _safe(context, "ttl_minutes", "60")
    requesting_ip = _safe(context, "requesting_ip", "")
    tenant_label = _safe(context, "tenant_label", "")

    tenant_block = (
        ui.muted(
            f"This reset applies to your account in <strong>{tenant_label}</strong>."
        )
        if tenant_label
        else ""
    )

    if requesting_ip:
        ip_block = ui.muted(
            f"Request came from <strong>{requesting_ip}</strong>. "
            "If that wasn't you, ignore this email — your password will not change."
        )
    else:
        ip_block = ui.muted(
            "If you didn't request this reset, ignore the email — your "
            "password will not change."
        )

    content = (
        ui.eyebrow("Password reset")
        + ui.heading(f"Hello {recipient_name},")
        + ui.paragraph(
            f"We received a request to reset your <strong>{platform_name}</strong> password."
        )
        + tenant_block
        + ui.button("Reset password", reset_url)
        + ui.fallback_link(reset_url)
        + ui.paragraph(
            f"The link expires in <strong>{ttl_minutes} minute(s)</strong> "
            "and can only be used once."
        )
        + ip_block
    )

    return ui.page(
        content,
        preheader="Reset your VisiChek password",
    )


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    reset_url = _safe(context, "reset_url", "")
    ttl_minutes = _safe(context, "ttl_minutes", "60")
    requesting_ip = _safe(context, "requesting_ip", "")
    tenant_label = _safe(context, "tenant_label", "")

    lines = [
        f"Hello {recipient_name},",
        "",
        f"We received a request to reset your {platform_name} password.",
    ]
    if tenant_label:
        lines.extend(["", f"This reset applies to your account in {tenant_label}."])

    if reset_url:
        lines.extend(
            ["", "Open this link to set a new password:", "", f"  {reset_url}"]
        )

    lines.extend(
        [
            "",
            f"The link expires in {ttl_minutes} minute(s) and can only be used once.",
        ]
    )

    if requesting_ip:
        lines.extend(
            [
                "",
                f"Request came from {requesting_ip}. If that wasn't you, "
                "ignore this email — your password will not change.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "If you didn't request this reset, ignore the email — "
                "your password will not change.",
            ]
        )

    lines.extend(ui.text_signoff())
    return "\n".join(lines)
