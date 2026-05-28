"""Registry of platform-managed tenant agreements.

Each agreement is authored by the application admin as a normal legal document
in ``/v1/legal-documents`` and recognised here by its reserved ``slug``. The
``key`` is the stable identifier used in the tenant-facing acceptance API
(``/v1/agreements/{key}``) and the per-tenant store; the ``slug`` ties the
agreement to its master legal document.

Adding a new agreement is a two-line change here plus (optionally) a committed
seed asset in this package — the gate, templating, acceptance API, seeding and
backfill all iterate over this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class AgreementSpec:
    """Static definition of one tenant agreement."""

    key: str
    """Stable identifier (``dpa``, ``visitor_privacy_policy``)."""

    title: str
    """Default display title (used before a master is published)."""

    slug: str
    """Reserved legal-document slug that holds the master template."""

    doc_type: str
    """``LegalDocType`` value the master is grouped under."""

    seed_asset: Optional[str] = None
    """Committed JSON asset (in this package) used to seed the master legal
    document at startup if it does not already exist. ``None`` = no auto-seed."""


# ---------------------------------------------------------------------------
# The two agreements
# ---------------------------------------------------------------------------

DPA = AgreementSpec(
    key="dpa",
    title="Data Processing Agreement",
    slug="data-processing-agreement",
    doc_type="data_processing_agreement",
    # The existing committed DPA blocks live next to the legacy dpa_defaults
    # asset and are reused as the seed fallback.
    seed_asset="dpa_template_blocks.json",
)

VISITOR_PRIVACY_POLICY = AgreementSpec(
    key="visitor_privacy_policy",
    title="Visitor Privacy Policy",
    slug="visitor-privacy-policy",
    doc_type="privacy_policy",
    seed_asset="visitor_privacy_policy_blocks.json",
)


_AGREEMENTS: Dict[str, AgreementSpec] = {
    DPA.key: DPA,
    VISITOR_PRIVACY_POLICY.key: VISITOR_PRIVACY_POLICY,
}

#: Stable iteration order for the gate / list endpoints.
ALL_AGREEMENT_KEYS: List[str] = [DPA.key, VISITOR_PRIVACY_POLICY.key]

#: Reverse lookup slug -> key, used by the legal publish writer to detect when
#: an agreement master was (re)published.
_SLUG_TO_KEY: Dict[str, str] = {a.slug: a.key for a in _AGREEMENTS.values()}


def get_agreement(key: str) -> Optional[AgreementSpec]:
    return _AGREEMENTS.get(key)


def all_agreements() -> List[AgreementSpec]:
    return [_AGREEMENTS[k] for k in ALL_AGREEMENT_KEYS]


def agreement_key_for_slug(slug: str) -> Optional[str]:
    """Return the agreement key a legal-document slug maps to, or ``None``."""
    return _SLUG_TO_KEY.get(slug)


def is_agreement_slug(slug: Optional[str]) -> bool:
    return bool(slug) and slug in _SLUG_TO_KEY
