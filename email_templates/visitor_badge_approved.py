"""Virtual-badge approval email (Issue 7).

Mounted at key ``visitor_badge_approved``. Sent to a visitor when
their check-in transitions to approved, the tenant has
``send_visitor_badge_email`` enabled, and the visitor supplied an
email address.

Carries:

* ``badge_page_url`` — the public printable-badge page on the
  frontend (``{APP_BASE_URL}/badge/{token}``). Rendered as the
  primary CTA, same style as the password-reset button.
* ``badge_url``      — best-effort presigned download for the
  pre-rendered PDF (when DocumentStorage is configured). Kept as a
  secondary link so visitors on locked-down inboxes still have a
  fallback.
* ``badge_qr_token`` — last-resort: the raw token a receptionist
  can type if every link in the email is stripped.

The token expires with the badge (default 24h) so a leaked email
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
    badge_page_url = _safe(context, "badge_page_url")
    badge_url = _safe(context, "badge_url")
    badge_qr_token = _safe(context, "badge_qr_token")
    expires_at = _safe(context, "expires_at_formatted")

    primary_cta = ""
    if badge_page_url:
        primary_cta = (
            "<p style='margin:0 0 18px'>"
            f"<a href='{badge_page_url}' style='display:inline-block;"
            "background:#0F172A;color:#FFFFFF;text-decoration:none;"
            "padding:12px 22px;border-radius:8px;font-weight:600;"
            "font-size:14px'>Open your badge</a>"
            "</p>"
        )

    fallback_link_block = ""
    if badge_page_url:
        fallback_link_block = (
            "<p style='margin:0 0 14px;color:#475569;font-size:13px'>"
            "If the button doesn't work, paste this URL into your browser:"
            "</p>"
            f"<p style='margin:0 0 18px;word-break:break-all;font-family:"
            "ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;"
            f"color:#0F172A'>{badge_page_url}</p>"
        )

    download_block = ""
    if badge_url:
        download_block = (
            f"<p><a href='{badge_url}' "
            "style='color:#0F172A;font-size:13px;text-decoration:underline'>"
            "Or download the PDF</a></p>"
        )

    qr_block = ""
    if badge_qr_token:
        qr_block = (
            "<p style='color:#666;font-size:13px'>If your inbox blocks every "
            "link, show this code at reception: "
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
        f"<p>{tenant_name} has approved your visit. Tap the button below to "
        "open your badge on your phone — the front desk will scan the QR to "
        "check you in instantly.</p>"
        f"{visit_meta_html}"
        f"{primary_cta}"
        f"{fallback_link_block}"
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
    badge_page_url = _safe(context, "badge_page_url")
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
    if badge_page_url:
        lines.extend(["", f"Open your badge: {badge_page_url}"])
    if badge_url:
        lines.append(f"PDF download: {badge_url}")
    if badge_qr_token:
        lines.append(f"Reception code: {badge_qr_token}")
    if expires_at:
        lines.append(f"Badge expires {expires_at}.")
    lines.extend(["", f"Sent by {tenant_name} via VisiChek."])
    return "\n".join(lines)
