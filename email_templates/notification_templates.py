"""Per-event notification email templates (Issue 6 / Phase B2).

One module that exposes the templates fired by the central
``send_notification()`` fan-out for each kind of event the
``NotificationPreferences`` schema can gate. Keeping them grouped in
one file keeps the mounted_templates registry compact and makes it
easy to scan "what events can email a user?".

Each template:

  - Accepts a ``recipient_name`` for the salutation.
  - Accepts a ``title`` and ``body`` carrying the in-app
    notification copy so admin-supplied per-event detail (the
    "30 minutes" string, the visitor's name, etc.) lands in the
    email automatically.
  - Optionally accepts a ``link`` to deep-link back into the app.
  - Optionally accepts a ``tenant_name`` for branding-friendly copy
    in the subject.

Each template module is exported as a ``MountedTemplate``-compatible
namespace (``TEMPLATE_KEY``, ``SUBJECT``, ``render_html``,
``render_text``) so ``core/email/mounted_templates.py`` can register
them with one ``_mount(...)`` call per template.

When adding a new ``notify_*`` helper to ``notification_service``,
add a matching template here so the email path doesn't 500 on the
"template not mounted" guard in ``EmailManager._render``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _safe(context: dict[str, Any], key: str, fallback: str = "") -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def _wrap_html(*, title_line: str, intro: str, body: str, link: str | None) -> str:
    cta = ""
    if link:
        cta = (
            f"<p style='margin:20px 0'><a href='{link}' "
            "style='display:inline-block;padding:10px 16px;background:#0f172a;"
            "color:#fff;border-radius:6px;text-decoration:none;font-weight:600'>"
            "Open in VisiChek</a></p>"
        )
    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;max-width:560px'>"
        f"<h2 style='margin:0 0 12px'>{title_line}</h2>"
        f"<p style='color:#444'>{intro}</p>"
        f"<p style='color:#444'>{body}</p>"
        f"{cta}"
        "<hr style='border:none;border-top:1px solid #e5e7eb;margin:20px 0' />"
        "<p style='color:#888;font-size:12px'>You're receiving this because "
        "the matching email toggle is on in your VisiChek notification settings.</p>"
        "</div>"
    )


def _wrap_text(*, title_line: str, intro: str, body: str, link: str | None) -> str:
    parts = [title_line, "", intro, "", body]
    if link:
        parts.extend(["", f"Open: {link}"])
    parts.extend([
        "",
        "—",
        "You're receiving this because the matching email toggle is on in "
        "your VisiChek notification settings.",
    ])
    return "\n".join(parts)


def _make_template(
    *,
    key: str,
    subject_template: str,
    title_template: str,
    intro_template: str,
) -> SimpleNamespace:
    """Factory that produces a ``MountedTemplate``-compatible namespace.

    Subject / title / intro templates use ``str.format`` with the
    incoming context — keys that aren't present render as an empty
    string via ``_safe``. The ``body`` field of the notification
    is always rendered verbatim as the main paragraph.
    """

    def render_html(context: dict[str, Any]) -> str:
        safe_context = {
            k: _safe(context, k, "") for k in {"title", "body", "link", "recipient_name", "tenant_name", "visitor_name", "appointment_id", "incident_id", "dsr_id", "message", "new_user_name"}
        }
        title_line = title_template.format(**safe_context)
        intro = intro_template.format(**safe_context)
        return _wrap_html(
            title_line=title_line,
            intro=intro,
            body=safe_context["body"] or safe_context["title"],
            link=safe_context["link"] or None,
        )

    def render_text(context: dict[str, Any]) -> str:
        safe_context = {
            k: _safe(context, k, "") for k in {"title", "body", "link", "recipient_name", "tenant_name", "visitor_name", "appointment_id", "incident_id", "dsr_id", "message", "new_user_name"}
        }
        title_line = title_template.format(**safe_context)
        intro = intro_template.format(**safe_context)
        return _wrap_text(
            title_line=title_line,
            intro=intro,
            body=safe_context["body"] or safe_context["title"],
            link=safe_context["link"] or None,
        )

    return SimpleNamespace(
        TEMPLATE_KEY=key,
        SUBJECT=subject_template,
        render_html=render_html,
        render_text=render_text,
    )


# ── Concrete templates ────────────────────────────────────────────


incident_deadline = _make_template(
    key="notif_incident_deadline",
    subject_template="Incident reporting deadline approaching",
    title_template="An incident is nearing its NDPC deadline",
    intro_template=(
        "Hello {recipient_name}, an incident in your tenant is approaching the "
        "72-hour NDPC notification window. Review it now so you don't miss the "
        "reporting deadline."
    ),
)


visitor_check_in = _make_template(
    key="notif_visitor_check_in",
    subject_template="{visitor_name} has checked in",
    title_template="{visitor_name} is here",
    intro_template=(
        "Hello {recipient_name}, your visitor {visitor_name} has checked in at "
        "reception and is waiting for you."
    ),
)


appointment_reminder = _make_template(
    key="notif_appointment_reminder",
    subject_template="Appointment with {visitor_name} in 30 minutes",
    title_template="Heads up — appointment soon",
    intro_template=(
        "Hello {recipient_name}, you have an appointment with {visitor_name} "
        "in 30 minutes."
    ),
)


dsr_submitted = _make_template(
    key="notif_dsr_submitted",
    subject_template="New data subject request",
    title_template="A new data subject request needs your attention",
    intro_template=(
        "Hello {recipient_name}, a new data subject request has landed in "
        "your DPO queue. Review and respond within the regulatory window."
    ),
)


subscription_alert = _make_template(
    key="notif_subscription_alert",
    subject_template="Subscription alert",
    title_template="Heads up — subscription change",
    intro_template=(
        "Hello {recipient_name}, an event on your subscription needs a quick "
        "look. {message}"
    ),
)


new_user_added = _make_template(
    key="notif_new_user_added",
    subject_template="New user added to your tenant",
    title_template="A new user joined your tenant",
    intro_template=(
        "Hello {recipient_name}, {new_user_name} was added to your "
        "organisation. If this wasn't expected, review your user list."
    ),
)


ALL_TEMPLATES = [
    incident_deadline,
    visitor_check_in,
    appointment_reminder,
    dsr_submitted,
    subscription_alert,
    new_user_added,
]
