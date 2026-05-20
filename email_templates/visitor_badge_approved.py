"""Virtual-badge approval email (Issue 7).

Mounted at key ``visitor_badge_approved``. Sent to a visitor when
their check-in transitions to approved, the tenant has
``send_visitor_badge_email`` enabled, and the visitor supplied an
email address.

Carries a signed badge download URL (or the badge QR token) rather
than the raw PDF bytes — bytes-as-attachment makes the dispatch
heavier and an inline link is what the kiosk already shows on-screen.
The link expires with the badge (default 24h) so a leaked email
can't be reused.
"""

from __future__ import annotations

from typing import Any

TEMPLATE_KEY = "visitor_badge_approved"
SUBJECT = "{tenant_name}: your visitor badge is ready"


def _safe(context: dict[str, Any], key: str, fallback: str = "") -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    visitor_name = _safe(context, "visitor_name", "there")
    tenant_name = _safe(context, "tenant_name", "VisiChek")
    host_name = _safe(context, "host_name")
    department = _safe(context, "department_name")
    badge_url = _safe(context, "badge_url")
    badge_qr_token = _safe(context, "badge_qr_token")
    expires_at = _safe(context, "expires_at_formatted")

    download_block = ""
    if badge_url:
        download_block = (
            f"<p><a href='{badge_url}' "
            "style='display:inline-block;padding:10px 16px;background:#0f172a;"
            "color:#fff;border-radius:6px;text-decoration:none;font-weight:600'>"
            "Download your badge</a></p>"
        )

    qr_block = ""
    if badge_qr_token:
        qr_block = (
            "<p style='color:#666;font-size:13px'>If your inbox blocks the "
            f"button above, show this code at reception: "
            f"<code style='background:#f3f4f6;padding:2px 6px;border-radius:4px'>{badge_qr_token}</code>"
            "</p>"
        )

    visit_meta = []
    if host_name:
        visit_meta.append(f"<strong>Host:</strong> {host_name}")
    if department:
        visit_meta.append(f"<strong>Department:</strong> {department}")
    visit_meta_html = (
        "<p style='color:#444;font-size:14px'>" + "<br/>".join(visit_meta) + "</p>"
        if visit_meta
        else ""
    )

    expiry_block = (
        f"<p style='color:#666;font-size:12px'>Badge expires {expires_at}.</p>"
        if expires_at
        else ""
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;max-width:560px'>"
        f"<h2 style='margin:0 0 8px'>You're approved, {visitor_name}.</h2>"
        f"<p>{tenant_name} has approved your visit. Open the badge link below "
        "on your phone before you arrive — the front desk will scan it to check "
        "you in instantly.</p>"
        f"{visit_meta_html}"
        f"{download_block}"
        f"{qr_block}"
        f"{expiry_block}"
        "<hr style='border:none;border-top:1px solid #e5e7eb;margin:20px 0' />"
        f"<p style='color:#888;font-size:12px'>Sent by {tenant_name} via VisiChek.</p>"
        "</div>"
    )


def render_text(context: dict[str, Any]) -> str:
    visitor_name = _safe(context, "visitor_name", "there")
    tenant_name = _safe(context, "tenant_name", "VisiChek")
    host_name = _safe(context, "host_name")
    department = _safe(context, "department_name")
    badge_url = _safe(context, "badge_url")
    badge_qr_token = _safe(context, "badge_qr_token")
    expires_at = _safe(context, "expires_at_formatted")

    lines = [
        f"You're approved, {visitor_name}.",
        "",
        f"{tenant_name} has approved your visit. Open the badge link below "
        "on your phone before you arrive.",
    ]
    if host_name:
        lines.append(f"Host: {host_name}")
    if department:
        lines.append(f"Department: {department}")
    if badge_url:
        lines.extend(["", f"Download your badge: {badge_url}"])
    if badge_qr_token:
        lines.append(f"Reception code: {badge_qr_token}")
    if expires_at:
        lines.append(f"Badge expires {expires_at}.")
    lines.extend(["", f"Sent by {tenant_name} via VisiChek."])
    return "\n".join(lines)
