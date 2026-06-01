"""Notification to a tenant when their support case has been successfully opened.

Sent immediately after case creation so the company knows VisiChek has
received their request, what priority level was assigned, and where to
track progress.
"""

from __future__ import annotations

import html
from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.opened.tenant"
SUBJECT = "We received your support case: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "case_priority": str(context.get("case_priority", "medium")),
        "company_name": str(context.get("tenant_company_name", "your organization")),
        "preview": str(context.get("message_preview", "")),
        "link": str(context.get("case_url", "#")),
        "case_id": str(context.get("case_id", "")),
    }


def _priority_variant(priority: str) -> str:
    """Map a priority string to a badge variant."""
    p = priority.lower()
    if p in ("critical", "urgent"):
        return "danger"
    if p in ("high",):
        return "warning"
    if p in ("low",):
        return "neutral"
    return "brand"


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    priority_badge = ui.badge(c["case_priority"], variant=_priority_variant(c["case_priority"]))
    preview_block = ui.quote(html.escape(c["preview"])) if c["preview"] else ""
    cta = ui.button("Track this case", c["link"] if c["link"] != "#" else "")
    case_id_note = ui.muted("Case ID: " + ui.code_chip(html.escape(c["case_id"]))) if c["case_id"] else ""

    content = (
        ui.eyebrow("Support")
        + ui.heading("We’ve received your case")
        + ui.paragraph(f"Hi {html.escape(c['company_name'])},")
        + ui.paragraph(
            f"Thanks for reaching out. Our team has received your support case "
            f"<strong>{html.escape(c['case_subject'])}</strong> with priority "
            + priority_badge + "."
        )
        + (ui.paragraph("Here’s a preview of what you sent:") if preview_block else "")
        + preview_block
        + cta
        + case_id_note
    )

    return ui.page(
        content,
        preheader="We received your support case",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'We\'ve received your support case "{c["case_subject"]}" '
        f"(priority: {c['case_priority']}).",
        "",
    ]
    if c["preview"]:
        lines += [f"Preview: {c['preview']}", ""]
    if c["link"] and c["link"] != "#":
        lines.append(f"Follow progress: {c['link']}")
    if c["case_id"]:
        lines.append(f"Case ID: {c['case_id']}")
    lines += ui.text_signoff()
    return "\n".join(lines)
