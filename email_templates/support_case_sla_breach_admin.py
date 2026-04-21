from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>SLA breach on a support case</h2>"
        f"<p>The SLA deadline has elapsed on:</p>"
        f"<ul>"
        f"<li>Tenant: <strong>{c['company_name']}</strong></li>"
        f"<li>Subject: {c['case_subject']}</li>"
        f"<li>Priority: {c['case_priority']}</li>"
        f"</ul>"
        f"<p><a href='{c['link']}'>Open case now</a></p>"
        f"<p>Case ID: <code>{c['case_id']}</code></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"SLA breach — Tenant: {c['company_name']} | "
        f"Subject: {c['case_subject']} | Priority: {c['case_priority']}\n\n"
        f"Open now: {c['link']}\nCase ID: {c['case_id']}"
    )
