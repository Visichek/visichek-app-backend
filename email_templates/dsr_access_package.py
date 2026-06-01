"""Right-of-Access data-export email.

Mounted at key ``dsr_access_package``. Sent to a data subject when a DPO
fulfils their access request: the backend gathers everything we hold about
them, packages it as a ZIP, stores it, and emails this secure download link.

Carries:

* ``download_url`` — presigned, time-limited (7-day) GET URL for the ZIP. The
  email system has no attachment support, so the link is the delivery channel.
* ``expires_at``   — Unix seconds the link stops working (rendered as a notice).
* ``visitor_name`` / ``tenant_name`` — greeting + sender label.
"""

from __future__ import annotations

import html
import time
from typing import Any

TEMPLATE_KEY = "dsr_access_package"
SUBJECT = "{tenant_name}: your personal data export is ready"


def _safe(context: dict[str, Any], key: str, fallback: str = "") -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def _format_expiry(context: dict[str, Any]) -> str:
    raw = context.get("expires_at")
    try:
        ts = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return time.strftime("%d %b %Y %H:%M UTC", time.gmtime(ts))


def render_html(context: dict[str, Any]) -> str:
    # Escape interpolated values for the HTML body. visitor_name is the
    # subject's self-entered name (untrusted); download_url is system-minted
    # but escaped so its query string can't break out of the href attribute.
    visitor_name = html.escape(_safe(context, "visitor_name", "there"))
    tenant_name = html.escape(_safe(context, "tenant_name", "VisiChek"))
    download_url = html.escape(_safe(context, "download_url"))
    expires_at = _format_expiry(context)

    primary_cta = ""
    if download_url:
        primary_cta = (
            "<p style='margin:0 0 18px'>"
            f"<a href='{download_url}' style='display:inline-block;"
            "background:#0F172A;color:#FFFFFF;text-decoration:none;"
            "padding:12px 22px;border-radius:8px;font-weight:600;"
            "font-size:14px'>Download your data</a>"
            "</p>"
        )

    fallback_link_block = ""
    if download_url:
        fallback_link_block = (
            "<p style='margin:0 0 14px;color:#475569;font-size:13px'>"
            "If the button doesn't work, paste this URL into your browser:"
            "</p>"
            "<p style='margin:0 0 18px;word-break:break-all;font-family:"
            "ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;"
            f"color:#0F172A'>{download_url}</p>"
        )

    expiry_block = (
        f"<p style='color:#666;font-size:12px'>This secure link expires {expires_at}. "
        "Download your file before then; contact us to request a fresh link if it "
        "lapses.</p>"
        if expires_at
        else (
            "<p style='color:#666;font-size:12px'>This secure link expires in 7 days.</p>"
        )
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;max-width:560px'>"
        f"<h2 style='margin:0 0 8px'>Your data export is ready, {visitor_name}.</h2>"
        f"<p>You asked {tenant_name} for a copy of the personal data we hold about "
        "you. We've packaged it into a ZIP file containing one spreadsheet per "
        "data category. Tap the button below to download it securely.</p>"
        f"{primary_cta}"
        f"{fallback_link_block}"
        f"{expiry_block}"
        "<hr style='border:none;border-top:1px solid #e5e7eb;margin:20px 0' />"
        f"<p style='color:#888;font-size:12px'>Sent by {tenant_name} via VisiChek "
        "in response to your data subject access request.</p>"
        "</div>"
    )


def render_text(context: dict[str, Any]) -> str:
    visitor_name = _safe(context, "visitor_name", "there")
    tenant_name = _safe(context, "tenant_name", "VisiChek")
    download_url = _safe(context, "download_url")
    expires_at = _format_expiry(context)

    lines = [
        f"Your data export is ready, {visitor_name}.",
        "",
        f"You asked {tenant_name} for a copy of the personal data we hold about "
        "you. It's packaged as a ZIP file with one spreadsheet per data category.",
    ]
    if download_url:
        lines.extend(["", f"Download your data: {download_url}"])
    if expires_at:
        lines.append(f"This secure link expires {expires_at}.")
    else:
        lines.append("This secure link expires in 7 days.")
    lines.extend(
        [
            "",
            f"Sent by {tenant_name} via VisiChek in response to your data subject "
            "access request.",
        ]
    )
    return "\n".join(lines)
