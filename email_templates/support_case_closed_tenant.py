from __future__ import annotations

from typing import Any

TEMPLATE_KEY = "support_case.closed.tenant"
SUBJECT = "Case closed: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"<h2>Your support case is now closed</h2>"
        f'<p>The case <strong>"{c["case_subject"]}"</strong> has been closed. '
        f"If something similar comes up, you can always open a new case.</p>"
        f"<p><a href='{c['link']}'>View case history</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f'The case "{c["case_subject"]}" has been closed. '
        f"Open a new case if you need anything else.\n\nHistory: {c['link']}"
    )
