"""Password-reset email template.

Sent by ``services/forgot_password_service.request_password_reset``
when an unauthenticated user submits ``POST /v1/auth/forgot-password``
with an email that matches a real account. Carries the single-use
reset link (token in the URL) and the friendly fallback for clients
that can't render HTML.

The endpoint deliberately returns the same 202 envelope regardless of
whether a match was found — so a no-match call produces NO email and
NO outbound metadata that would let an attacker enumerate accounts.
"""

from __future__ import annotations

from typing import Any

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
    token = _safe(context, "token", "")
    ttl_minutes = _safe(context, "ttl_minutes", "60")
    requesting_ip = _safe(context, "requesting_ip", "")
    tenant_label = _safe(context, "tenant_label", "")

    button_block = (
        f"<p style='margin:0 0 18px'>"
        f"<a href='{reset_url}' style='display:inline-block;background:#0F172A;"
        "color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:8px;"
        "font-weight:600;font-size:14px'>Reset password</a>"
        "</p>"
        if reset_url
        else ""
    )

    fallback_link_block = (
        "<p style='margin:0 0 14px;color:#475569;font-size:13px'>"
        "If the button doesn't work, paste this URL into your browser:</p>"
        f"<p style='margin:0 0 18px;word-break:break-all;font-family:"
        f"ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;"
        f"color:#0F172A'>{reset_url}</p>"
        if reset_url
        else (
            "<p style='margin:0 0 6px;color:#475569;font-size:13px'>"
            "Use this token in the reset form:</p>"
            "<p style='margin:0 0 18px;font-family:ui-monospace,SFMono-Regular,"
            f"Menlo,monospace;font-size:14px;font-weight:600'>{token}</p>"
        )
    )

    tenant_block = (
        f"<p style='margin:0 0 14px;color:#475569;font-size:13px'>"
        f"This reset applies to your account in <strong>{tenant_label}</strong>.</p>"
        if tenant_label
        else ""
    )

    ip_block = (
        f"<p style='margin:0;color:#64748B;font-size:12px'>"
        f"Request came from <strong>{requesting_ip}</strong>. "
        "If that wasn't you, ignore this email — your password will not change."
        "</p>"
        if requesting_ip
        else (
            "<p style='margin:0;color:#64748B;font-size:12px'>"
            "If you didn't request this reset, ignore the email — your "
            "password will not change."
            "</p>"
        )
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;"
        "max-width:560px;color:#0F172A;line-height:1.55'>"
        f"<h2 style='margin:0 0 14px;font-size:20px'>Hello {recipient_name},</h2>"
        f"<p style='margin:0 0 18px'>We received a request to reset your "
        f"<strong>{platform_name}</strong> password.</p>"
        f"{tenant_block}"
        f"{button_block}"
        f"{fallback_link_block}"
        f"<p style='margin:0 0 14px'>The link expires in <strong>{ttl_minutes}"
        " minute(s)</strong> and can only be used once.</p>"
        f"{ip_block}"
        "</div>"
    )


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    reset_url = _safe(context, "reset_url", "")
    token = _safe(context, "token", "")
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
        lines.extend(["", "Open this link to set a new password:", "", f"  {reset_url}"])
    else:
        lines.extend(["", "Use this token in the reset form:", "", f"  {token}"])

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
    return "\n".join(lines)
