"""Application-admin login OTP email.

Sent by ``services/otp_service.create_otp_challenge`` whenever an
admin completes step 1 of login (email + password) and 2FA is
required. Carries the one-time code that must be submitted to
``POST /v1/admins/verify-otp`` together with the challenge id.

The env-configured primary admin (id ``656f7ac12b9d4f6c9e2b9f7d``)
does NOT receive this email — they keep using the static dev code
from ``OTP_DEV_CODE`` so on-call recovery never depends on email
delivery. Every other admin must use the code in this email.
"""

from __future__ import annotations

from typing import Any

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

    ip_block = (
        f"<p style='margin:0;color:#64748B;font-size:12px'>"
        f"Request came from <strong>{requesting_ip}</strong>. "
        "If that wasn't you, change your password immediately."
        "</p>"
        if requesting_ip
        else (
            "<p style='margin:0;color:#64748B;font-size:12px'>"
            "If you didn't try to sign in, change your password immediately."
            "</p>"
        )
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;"
        "max-width:520px;color:#0F172A;line-height:1.55'>"
        f"<h2 style='margin:0 0 14px;font-size:20px'>Hello {recipient_name},</h2>"
        f"<p style='margin:0 0 18px'>Use this code to finish signing in to "
        f"<strong>{platform_name}</strong>.</p>"
        "<div style='background:#0F172A;color:#FFFFFF;border-radius:12px;"
        "padding:22px 28px;margin:0 0 18px;text-align:center'>"
        "<p style='margin:0 0 6px;font-size:12px;text-transform:uppercase;"
        "letter-spacing:0.08em;color:#94A3B8'>Verification code</p>"
        f"<p style='margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
        f"font-size:34px;font-weight:700;letter-spacing:0.18em'>{code}</p>"
        "</div>"
        f"<p style='margin:0 0 14px'>The code expires in <strong>{ttl_minutes}"
        " minute(s)</strong> and can only be used once.</p>"
        f"{ip_block}"
        "</div>"
    )


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
    return "\n".join(lines)
