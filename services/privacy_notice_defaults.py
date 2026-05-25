"""VisiChek-style default visitor privacy notice (APPENDIX 1).

Seeded on tenant provisioning (and lazily on first read of the active notice)
so the visitor-consent feature works on day one without the tenant authoring
anything. The tenant can fully edit the seeded notice afterwards.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_NOTICE_TITLE = "Visitor Privacy Notice"

_DEFAULT_SUMMARY = (
    "{{COMPANY_NAME}} uses VisiChek to manage visitor check-in. We collect the "
    "personal details you provide here (such as your name, phone number, and the "
    "purpose of your visit) only to register your visit, keep our premises secure, "
    "and meet our legal and safety obligations. By continuing, you confirm you have "
    "read this notice and consent to your information being processed for these "
    "purposes."
)

_DEFAULT_FULL_TEXT = """WHO PROCESSES YOUR DATA
{{COMPANY_NAME}} is the data controller for the information collected during
visitor check-in. VisiChek acts as a data processor on {{COMPANY_NAME}}'s behalf
and processes your data only on its documented instructions.

WHAT WE COLLECT
- Identity and contact details you enter (e.g. full name, phone number, email,
  company/organisation).
- Visit details (host, department, purpose of visit, time of arrival and
  departure).
- Where enabled by {{COMPANY_NAME}}: a photograph, a scan of a government-issued
  ID for verification, and your approximate location at check-in for site
  safety/geofencing.

WHY WE COLLECT IT (PURPOSE)
- To register and manage your visit and produce a visitor badge.
- To maintain a secure record of who is on the premises for safety, security,
  and emergency/evacuation purposes.
- To comply with legal, regulatory, and health-and-safety obligations.

LAWFUL BASIS
We rely on your consent for this check-in and, where applicable, on our
legitimate interest in keeping our premises and people safe, in line with the
Nigeria Data Protection Act (NDPA).

HOW LONG WE KEEP IT
Your visit records are retained only as long as necessary for the purposes
above and in accordance with {{COMPANY_NAME}}'s retention policy, after which
they are deleted or anonymised.

WHO WE SHARE IT WITH
We do not sell your data. It may be shared with {{COMPANY_NAME}} staff who need
it to host or approve your visit, and with service providers (such as VisiChek)
under appropriate data-protection terms. We disclose data to authorities only
where required by law.

YOUR RIGHTS
Under the NDPA you have the right to access, correct, or request deletion of
your personal data, and to withdraw consent. To exercise these rights, contact
{{COMPANY_NAME}}{{DPO_CONTACT_LINE}}.

YOUR CHOICE
Providing this information is voluntary, but we may be unable to authorise your
visit without it. By ticking "I accept" you confirm you have read and understood
this notice."""


def _substitute(text: str, company_name: str, dpo_contact_email: Optional[str]) -> str:
    dpo_line = f" at {dpo_contact_email}" if dpo_contact_email else ""
    return text.replace("{{COMPANY_NAME}}", company_name or "Our organisation").replace(
        "{{DPO_CONTACT_LINE}}", dpo_line
    )


def build_default_notice_content(
    company_name: str,
    dpo_contact_email: Optional[str] = None,
) -> dict:
    """Return the seeded default notice's ``title`` / ``summary`` / ``full_text``
    with ``{{COMPANY_NAME}}`` and the optional DPO contact line substituted."""
    return {
        "title": DEFAULT_NOTICE_TITLE,
        "summary": _substitute(_DEFAULT_SUMMARY, company_name, dpo_contact_email),
        "full_text": _substitute(_DEFAULT_FULL_TEXT, company_name, dpo_contact_email),
    }
