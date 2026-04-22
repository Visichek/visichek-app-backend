from __future__ import annotations

from typing import Any

TEMPLATE_KEY = "support_case.resolved.tenant"
SUBJECT = "Your case has been resolved: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "actor_name": str(context.get("actor_name", "Our support team")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"<h2>Your case has been resolved</h2>"
        f"<p>{c['actor_name']} has marked your case "
        f'<strong>"{c["case_subject"]}"</strong> as resolved.</p>'
        f"<p>If the fix worked, you don't have to do anything — we'll auto-close "
        f"the case in 7 days. If something still isn't right, open the case "
        f"and click <em>Reopen</em>.</p>"
        f"<p><a href='{c['link']}'>Confirm or reopen</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f'{c["actor_name"]} has marked your case "{c["case_subject"]}" as '
        f"resolved.\n\nIf the fix worked, no action needed — it'll auto-close "
        f"in 7 days. Otherwise, reopen it from: {c['link']}"
    )
