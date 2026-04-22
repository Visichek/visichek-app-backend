from __future__ import annotations

from typing import Any

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


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"<h2>We've received your support case</h2>"
        f"<p>Hi {c['company_name']},</p>"
        f"<p>Thanks for reaching out. Our team has received your support case "
        f'<strong>"{c["case_subject"]}"</strong> (priority: {c["case_priority"]}).</p>'
        f"<p>Here's a preview of what you sent:</p>"
        f"<blockquote>{c['preview']}</blockquote>"
        f"<p>You can follow progress here: <a href='{c['link']}'>{c['link']}</a></p>"
        f"<p>Case ID: <code>{c['case_id']}</code></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f'We\'ve received your support case "{c["case_subject"]}" '
        f"(priority: {c['case_priority']}).\n\n"
        f"Preview: {c['preview']}\n\n"
        f"Follow progress: {c['link']}\n"
        f"Case ID: {c['case_id']}"
    )
