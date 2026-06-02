"""Notifies an admin that a support case has been assigned to them.

Sent whenever the VisiChek platform routes (or manually assigns) a tenant
support case to an admin user, giving them an immediate link to the case
queue and the key metadata they need to triage.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.assigned.admin"
SUBJECT = "[Support] {tenant_company_name} case assigned: {case_subject}"


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

    # Map priority string to a badge variant so it stands out visually.
    _priority_variant = {
        "high": "danger",
        "urgent": "danger",
        "medium": "warning",
        "low": "neutral",
    }.get(c["case_priority"].lower(), "neutral")

    content = (
        ui.eyebrow("Case assigned")
        + ui.heading("A support case has been assigned to you")
        + ui.meta_rows(
            [
                ("Tenant", c["company_name"]),
                ("Subject", c["case_subject"]),
                ("Priority", ui.badge(c["case_priority"], variant=_priority_variant)),
            ]
        )
        + ui.button("Open case", c["link"])
        + ui.muted("Case ID: " + ui.code_chip(c["case_id"]) if c["case_id"] else "")
    )

    return ui.page(
        content,
        preheader="A support case was assigned to you",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        "A support case has been assigned to you.",
        "",
        f"Tenant:   {c['company_name']}",
        f"Subject:  {c['case_subject']}",
        f"Priority: {c['case_priority']}",
        "",
        f"Open case: {c['link']}",
    ]
    if c["case_id"]:
        lines.append(f"Case ID: {c['case_id']}")
    lines.extend(ui.text_signoff())
    return "\n".join(lines)
