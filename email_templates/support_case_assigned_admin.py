from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>A support case has been assigned to you</h2>"
        f"<p>Tenant: <strong>{c['company_name']}</strong><br/>"
        f"Subject: {c['case_subject']}<br/>"
        f"Priority: {c['case_priority']}</p>"
        f"<p><a href='{c['link']}'>Open case</a></p>"
        f"<p>Case ID: <code>{c['case_id']}</code></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"A support case has been assigned to you.\n"
        f"Tenant: {c['company_name']}\n"
        f"Subject: {c['case_subject']}\n"
        f"Priority: {c['case_priority']}\n\n"
        f"Open: {c['link']}\nCase ID: {c['case_id']}"
    )
