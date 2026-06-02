"""VisiChek admin notification: a new support case has been opened.

Sent to VisiChek staff whenever a tenant submits a support case. Includes
the company name, support tier, subject, priority badge, category, a preview
of the opening message, a direct link to the admin console, and the case ID.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.opened.admin"
SUBJECT = "[Support] New case from {tenant_company_name}: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "case_priority": str(context.get("case_priority", "medium")),
        "case_category": str(context.get("case_category", "other")),
        "company_name": str(context.get("tenant_company_name", "A tenant")),
        "preview": str(context.get("message_preview", "")),
        "link": str(context.get("case_url", "#")),
        "case_id": str(context.get("case_id", "")),
        "support_tier": str(context.get("support_tier", "none")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    priority_badge = ui.badge(c["case_priority"], variant="neutral")

    content = (
        ui.eyebrow("New support case")
        + ui.heading("New support case opened")
        + ui.paragraph(
            f"<strong>{c['company_name']}</strong> opened a support case "
            f"(tier: {c['support_tier']})."
        )
        + ui.meta_rows(
            [
                ("Subject", c["case_subject"]),
                ("Priority", priority_badge),
                ("Category", c["case_category"]),
            ]
        )
        + ui.quote(c["preview"])
        + ui.button("Open in admin console", c["link"])
        + ui.muted("Case ID: " + ui.code_chip(c["case_id"]))
    )

    return ui.page(content, preheader="New support case opened")


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f"{c['company_name']} opened a support case (tier: {c['support_tier']}).",
        f"Subject: {c['case_subject']} | Priority: {c['case_priority']} | "
        f"Category: {c['case_category']}",
        "",
        f"Preview: {c['preview']}",
        "",
        f"Admin console: {c['link']}",
        f"Case ID: {c['case_id']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
