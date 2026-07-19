"""Every email template key referenced in code must be mounted.

Regression guard for the ``onboarding_rejected`` / ``onboarding_completed``
bug: the service queued those keys long before the template modules
existed, so the worker raised "Template ... is not mounted" and every
rejection / completion email silently vanished. Same class of bug: the
dunning path queued a task that didn't exist — ``billing_dunning`` now
covers those sends.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from core.email.mounted_templates import get_mounted_templates

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Direct keyword references: template_key="..." / email_template_key="..."
TEMPLATE_KEY_RE = re.compile(r'(?:email_)?template_key\s*=\s*"([a-z0-9_.]+)"')

# Keys that reach the mailer through dict lookups / variables, which the
# regex above can't see. Pin them explicitly.
INDIRECT_KEYS = [
    "support_case.opened.tenant",
    "support_case.acknowledged.tenant",
    "support_case.admin_replied.tenant",
    "support_case.awaiting_tenant",
    "support_case.resolved.tenant",
    "support_case.closed.tenant",
    "support_case.opened.admin",
    "support_case.assigned.admin",
    "support_case.tenant_replied.admin",
    "support_case.sla_breach.admin",
    "dsr_in_progress",
    "dsr_completed",
    "onboarding_partial_accepted",
    "billing_dunning",
]


def _mounted_keys() -> set[str]:
    return {t.key for t in get_mounted_templates()}


def _referenced_keys() -> set[str]:
    keys: set[str] = set()
    for folder in ("services", "api", "core"):
        for path in (BACKEND_ROOT / folder).rglob("*.py"):
            keys.update(TEMPLATE_KEY_RE.findall(path.read_text(encoding="utf-8")))
    return keys


def test_every_referenced_template_key_is_mounted() -> None:
    mounted = _mounted_keys()
    referenced = _referenced_keys()
    missing = sorted(k for k in referenced if k not in mounted)
    assert not missing, (
        f"Template keys referenced in code but not mounted: {missing}. "
        "Queued sends for these keys fail on the worker with "
        "'Template ... is not mounted' and the email silently vanishes."
    )


def test_indirectly_referenced_keys_are_mounted() -> None:
    mounted = _mounted_keys()
    missing = sorted(k for k in INDIRECT_KEYS if k not in mounted)
    assert not missing, f"Indirect template keys not mounted: {missing}"


def test_mounted_keys_are_unique() -> None:
    keys = [t.key for t in get_mounted_templates()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    "module_name",
    ["onboarding_rejected", "onboarding_completed", "billing_dunning"],
)
def test_new_templates_render_html_and_text(module_name: str) -> None:
    module = importlib.import_module(f"email_templates.{module_name}")
    ctx = {
        "full_name": "Ada Lovelace",
        "recipient_name": "Ada Lovelace",
        "platform_name": "VisiChek",
        "organization_name": "Acme Corp",
        "review_notes": "Company registration number could not be verified.",
        "login_url": "https://client.visichek.app/app/login",
        "billing_url": "https://client.visichek.app/app/billing",
        "subject_line": "Payment failed",
        "title": "Payment failed",
        "body": "We could not process your payment.",
    }
    html = module.render_html(ctx)
    text = module.render_text(ctx)
    assert isinstance(html, str) and html
    assert isinstance(text, str) and text
    assert "Ada Lovelace" in html
    assert "Ada Lovelace" in text

    # Sparse legacy contexts must not raise — fallbacks handle it.
    assert module.render_html({})
    assert module.render_text({})


def test_onboarding_subjects_format() -> None:
    rejected = importlib.import_module("email_templates.onboarding_rejected")
    completed = importlib.import_module("email_templates.onboarding_completed")
    assert "VisiChek" in rejected.SUBJECT.format(platform_name="VisiChek")
    assert "VisiChek" in completed.SUBJECT.format(platform_name="VisiChek")


def test_billing_dunning_subject_comes_from_context() -> None:
    dunning = importlib.import_module("email_templates.billing_dunning")
    assert (
        dunning.SUBJECT.format(subject_line="Final payment notice")
        == "Final payment notice"
    )
