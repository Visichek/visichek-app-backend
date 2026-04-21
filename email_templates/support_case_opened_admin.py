from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>New support case opened</h2>"
        f"<p><strong>{c['company_name']}</strong> opened a support case "
        f"(tier: {c['support_tier']}).</p>"
        f"<ul>"
        f"<li>Subject: {c['case_subject']}</li>"
        f"<li>Priority: {c['case_priority']}</li>"
        f"<li>Category: {c['case_category']}</li>"
        f"</ul>"
        f"<blockquote>{c['preview']}</blockquote>"
        f"<p><a href='{c['link']}'>Open case in admin console</a></p>"
        f"<p>Case ID: <code>{c['case_id']}</code></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"{c['company_name']} opened a support case (tier: {c['support_tier']}).\n"
        f"Subject: {c['case_subject']} | Priority: {c['case_priority']} | "
        f"Category: {c['case_category']}\n\n"
        f"Preview: {c['preview']}\n\n"
        f"Admin console: {c['link']}\n"
        f"Case ID: {c['case_id']}"
    )
