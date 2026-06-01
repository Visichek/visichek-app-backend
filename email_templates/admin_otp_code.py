"""Application-admin login OTP email.

Sent by ``services/otp_service.create_otp_challenge`` whenever an
admin completes step 1 of login (email + password) and 2FA is
required. Carries the one-time code that must be submitted to
``POST /v1/admins/verify-otp`` together with the challenge id.

The env-configured primary admin (id ``656f7ac12b9d4f6c9e2b9f7d``)
does NOT receive this email — they keep using the static dev code
from ``OTP_DEV_CODE`` so on-call recovery never depends on email
delivery. Every other admin must use the code in this email.

Presentation uses the shared VisiChek brand shell (``_shell.py``).
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "admin_otp_code"
SUBJECT = "Your {platform_name} sign-in code"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    code = _safe(context, "code", "------")
    ttl_minutes = _safe(context, "ttl_minutes", "5")
    requesting_ip = _safe(context, "requesting_ip", "")

    if requesting_ip:
        ip_block = ui.muted(
            f"Request came from <strong>{requesting_ip}</strong>. "
            "If that wasn't you, change your password immediately."
        )
    else:
        ip_block = ui.muted(
            "If you didn't try to sign in, change your password immediately."
        )

    content = (
        ui.eyebrow("Sign-in code")
        + ui.heading(f"Hello {recipient_name},")
        + ui.paragraph(
            f"Use this code to finish signing in to <strong>{platform_name}</strong>."
        )
        + ui.code_box(code, label="Verification code")
        + ui.paragraph(
            f"The code expires in <strong>{ttl_minutes} minute(s)</strong> "
            "and can only be used once."
        )
        + ip_block
    )

    return ui.page(content, preheader="Your VisiChek sign-in code")


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    code = _safe(context, "code", "------")
    ttl_minutes = _safe(context, "ttl_minutes", "5")
    requesting_ip = _safe(context, "requesting_ip", "")

    lines = [
        f"Hello {recipient_name},",
        "",
        f"Use this code to finish signing in to {platform_name}:",
        "",
        f"  {code}",
        "",
        f"The code expires in {ttl_minutes} minute(s) and can only be used once.",
    ]
    if requesting_ip:
        lines.extend(
            [
                "",
                f"Request came from {requesting_ip}. If that wasn't you, "
                "change your password immediately.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "If you didn't try to sign in, change your password immediately.",
            ]
        )
    lines.extend(ui.text_signoff())
    return "\n".join(lines)
