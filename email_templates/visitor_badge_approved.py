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
can't be reused. Presentation is handled entirely by the shared
``_shell`` brand layer (visichek.app design language).
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

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

    download_block = ""
    if badge_url:
        download_block = ui.paragraph(f"<a href='{badge_url}'>Or download the PDF</a>")

    qr_block = ""
    if badge_qr_token:
        qr_block = ui.muted(
            "If your inbox blocks every link, show this code at reception: "
            + ui.code_chip(badge_qr_token)
        )

    expiry_block = ""
    if expires_at:
        expiry_block = ui.muted(f"Badge expires {expires_at}.")

    content = (
        ui.eyebrow("Visitor badge")
        + ui.heading(f"You’re approved, {visitor_name}.")
        + ui.paragraph(
            f"{tenant_name} has approved your visit. Tap the button below to "
            "open your badge on your phone — the front desk will scan the QR to "
            "check you in instantly."
        )
        + ui.meta_rows([("Host", host_name), ("Department", department)])
        + ui.button("Open your badge", badge_page_url)
        + ui.fallback_link(badge_page_url)
        + download_block
        + qr_block
        + expiry_block
    )

    return ui.page(
        content,
        preheader="Your visitor badge is ready",
        footer_note_html=f"Sent by {tenant_name} via VisiChek.",
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
    lines.extend(ui.text_signoff(sender_label=f"Sent by {tenant_name} via VisiChek."))
    return "\n".join(lines)
