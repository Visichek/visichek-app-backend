from __future__ import annotations

from typing import Any

TEMPLATE_KEY = "support_case.acknowledged.tenant"
SUBJECT = "We've acknowledged your case: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "actor_name": str(context.get("actor_name", "Our support team")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"<h2>Your support case has been acknowledged</h2>"
        f"<p>{c['actor_name']} has acknowledged your case "
        f"<strong>\"{c['case_subject']}\"</strong> and is reviewing it now.</p>"
        f"<p><a href='{c['link']}'>View case</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"{c['actor_name']} has acknowledged your case \"{c['case_subject']}\" "
        f"and is reviewing it now.\n\nView case: {c['link']}"
    )
