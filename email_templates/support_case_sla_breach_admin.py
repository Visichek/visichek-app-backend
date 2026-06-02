"""Admin alert email sent when a support case has breached its SLA deadline.

Notifies VisiChek staff that the SLA deadline has elapsed on an open case,
surfacing the tenant, subject, and priority so the team can act immediately.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.sla_breach.admin"
SUBJECT = "[SLA Breach] {tenant_company_name}: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "case_priority": str(context.get("case_priority", "medium")),
        "company_name": str(context.get("tenant_company_name", "A tenant")),
        "link": str(context.get("case_url", "#")),
        "case_id": str(context.get("case_id", "")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    case_id_note = (
        ui.muted("Case ID: " + ui.code_chip(c["case_id"])) if c["case_id"] else ""
    )

    content = (
        ui.eyebrow("SLA breach", variant="danger")
        + ui.heading("SLA breach on a support case")
        + ui.paragraph(
            "The SLA deadline has elapsed on the following case. Immediate attention is required."
        )
        + ui.panel(
            ui.meta_rows(
                [
                    ("Tenant", c["company_name"]),
                    ("Subject", c["case_subject"]),
                    ("Priority", ui.badge(c["case_priority"], variant="danger")),
                ]
            ),
            variant="danger",
        )
        + ui.button("Open case now", c["link"])
        + ui.fallback_link(c["link"])
        + case_id_note
    )

    return ui.page(
        content,
        preheader="SLA breach on a support case",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        "[ SLA BREACH ]",
        "",
        "SLA breach on a support case",
        "",
        "The SLA deadline has elapsed on the following case.",
        "",
        f"Tenant:   {c['company_name']}",
        f"Subject:  {c['case_subject']}",
        f"Priority: {c['case_priority']}",
        "",
        f"Open case now: {c['link']}",
    ]
    if c["case_id"]:
        lines.append(f"Case ID: {c['case_id']}")
    lines += ui.text_signoff()
    return "\n".join(lines)
