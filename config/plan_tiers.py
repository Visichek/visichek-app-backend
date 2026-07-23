"""Canonical plan tier catalog — single source of truth for pricing tiers.

This module defines the four canonical plans (Free, Starter, Premium,
Enterprise). It is the source of truth for:

* Pricing — ``base_price_monthly`` / ``base_price_yearly``
* Feature gating — which endpoint patterns each tier may hit
* Hard caps — branches, departments, visitors/month, system users
* Per-tier admin adjustability — which ``tenant_caps`` an application
  admin is allowed to override at the plan level

Anything not listed in ``ADJUSTABLE_FIELDS`` for a tier is hard-baked
into the gate and cannot be edited via ``PUT /v1/plans/{id}``. Anything
listed CAN be edited but only at the cap-numeric level (you cannot turn
free-tier into premium by toggling features — features stay where they
are).

The helpers below are imported by ``services.plan_bootstrap`` to upsert
the canonical plans on app startup, and by ``services.plan_service`` to
enforce per-tier editability on ``PlanUpdate`` payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from config.platform_config import get_platform_config
from schemas.imports import SupportTier
from schemas.plan_schema import (
    CrudLimit,
    FeatureRule,
    PlanTier,
    QuotaResetInterval,
    RetrievalQuota,
    StorageLimit,
    TenantCapLimit,
)


# Singleton plan slug names. ``add_plan`` keys plans by name, so these
# are the durable identifiers across redeploys / migrations.
#
# These three plans are SINGLETONS — there is exactly one row per slug
# in MongoDB and the bootstrap upserts them on every startup. Frontend
# and backend both look them up by name.
FREE_PLAN_NAME = "free"
STARTER_PLAN_NAME = "starter"
PREMIUM_PLAN_NAME = "premium"

SINGLETON_PLAN_NAMES: Tuple[str, ...] = (
    FREE_PLAN_NAME,
    STARTER_PLAN_NAME,
    PREMIUM_PLAN_NAME,
)

# Free-trial length (in days) applied to the PAID singleton plans
# (Starter, Premium). Sourced from the deploy-time platform config so
# there is a single knob to change. Free and Enterprise never carry a
# trial: Free is already free, and Enterprise is bespoke / custom-priced
# per tenant (sales-quoted), so it is excluded by design.
PAID_PLAN_TRIAL_DAYS: int = get_platform_config().default_trial_days

# Enterprise plans, by contrast, are bespoke — sales/admin creates one
# per customer with a unique slug (the plan ``name``) so each tenant on
# Enterprise can have its own custom feature set, caps, and even its
# own FastAPI sub-app of endpoints (see ``core.enterprise_apps``). The
# frontend filters them out of the public catalogue and surfaces a
# "Contact sales" CTA instead.
#
# A single placeholder template name is reserved so old data (legacy
# ``"enterprise"`` plan rows) keeps resolving — but new plans should
# adopt distinct slugs like ``"enterprise-acme"`` or ``"acme-corp"``.
ENTERPRISE_TEMPLATE_NAME = "enterprise"

# Backwards-compatible alias kept for callers that referenced the
# legacy ``CANONICAL_PLAN_NAMES`` tuple. New code should prefer
# ``SINGLETON_PLAN_NAMES`` — there is no canonical Enterprise slug
# anymore.
CANONICAL_PLAN_NAMES: Tuple[str, ...] = SINGLETON_PLAN_NAMES


# Endpoint patterns we gate per tier. Anything NOT covered by a feature
# rule defaults to "allowed" (the existing middleware contract).
#
# To deny a feature on a tier, add a FeatureRule with enabled=False.
# Patterns use fnmatch syntax — ``/v1/foo/*`` matches ``/v1/foo`` and
# everything below it.
def _deny(
    pattern: str, description: str, methods: Optional[List[str]] = None
) -> FeatureRule:
    return FeatureRule(
        endpoint_pattern=pattern,
        methods=methods or ["GET", "POST", "PUT", "PATCH", "DELETE"],
        enabled=False,
        description=description,
    )


def _allow(
    pattern: str, description: str, methods: Optional[List[str]] = None
) -> FeatureRule:
    return FeatureRule(
        endpoint_pattern=pattern,
        methods=methods or ["GET", "POST", "PUT", "PATCH", "DELETE"],
        enabled=True,
        description=description,
    )


# Feature gates (denials) per tier. Order matters: a feature listed
# here is BLOCKED for that tier. We list the FREE denials then layer
# Starter as ``FREE_DENIALS - <stuff Starter unlocks>``, and so on.
FREE_DENIED_FEATURES: List[FeatureRule] = [
    # No appointments — manual reception flow only
    _deny("/v1/appointments*", "Appointments require Premium or Enterprise"),
    _deny("/v1/appointments/*", "Appointments require Premium or Enterprise"),
    # No host roster — hosts are part of the appointments/Premium surface
    _deny("/v1/hosts*", "Host management requires Premium or Enterprise"),
    _deny("/v1/hosts/*", "Host management requires Premium or Enterprise"),
    # No branding (logos, badge styling)
    _deny("/v1/branding*", "Custom branding requires Premium or Enterprise"),
    _deny("/v1/branding/*", "Custom branding requires Premium or Enterprise"),
    # No badge printing
    _deny("/v1/badges*", "Badge printing requires Starter or higher"),
    _deny("/v1/badges/*", "Badge printing requires Starter or higher"),
    # No KYC / ID verification
    _deny("/v1/kyc*", "ID verification requires Premium or Enterprise"),
    _deny("/v1/kyc/*", "ID verification requires Premium or Enterprise"),
    # No CSV export of dashboard / compliance
    _deny(
        "/v1/dashboard/export*",
        "CSV export requires Premium or Enterprise",
        methods=["GET"],
    ),
    _deny(
        "/v1/compliance/export*",
        "CSV export requires Premium or Enterprise",
        methods=["GET"],
    ),
    # No host notifications channel — only blocks the notification
    # preferences endpoint surface that drives host emails.
    _deny(
        "/v1/notifications/preferences",
        "Host email notifications require Premium or Enterprise",
        methods=["PUT", "PATCH"],
    ),
    # No QR self check-in (kiosk path stays open; the gating is on
    # tenant-side QR config which Starter unlocks too — see below).
    # No multi-location: branches above one are blocked by tenant_caps,
    # but POST/PUT/DELETE on branches gets a friendly tier-level deny.
    _deny(
        "/v1/branches",
        "Multi-location requires Premium or Enterprise",
        methods=["POST"],
    ),
    _deny(
        "/v1/branches/*",
        "Multi-location requires Premium or Enterprise",
        methods=["POST", "PUT", "PATCH", "DELETE"],
    ),
    # ── Compliance & governance — blocked entirely on Free ────────────
    _deny("/v1/incidents", "Incident logging requires Premium or Enterprise"),
    _deny("/v1/incidents/*", "Incident logging requires Premium or Enterprise"),
    _deny("/v1/dsr", "Data subject requests require Premium or Enterprise"),
    _deny("/v1/dsr/*", "Data subject requests require Premium or Enterprise"),
    _deny(
        "/v1/sub-processors", "Sub-processor register requires Premium or Enterprise"
    ),
    _deny(
        "/v1/sub-processors/*", "Sub-processor register requires Premium or Enterprise"
    ),
    _deny("/v1/retention-policies", "Retention policies require Premium or Enterprise"),
    _deny(
        "/v1/retention-policies/*", "Retention policies require Premium or Enterprise"
    ),
    _deny("/v1/audit-logs", "Audit log access requires Premium or Enterprise"),
    _deny("/v1/audit-logs/*", "Audit log access requires Premium or Enterprise"),
    _deny("/v1/compliance/*", "Compliance register requires Premium or Enterprise"),
    # ── Premium productivity / OCR — blocked entirely on Free ─────────
    _deny("/v1/face-crop", "Badge face-crop requires Premium or Enterprise"),
    _deny("/v1/face-crop/*", "Badge face-crop requires Premium or Enterprise"),
    _deny("/v1/id-extractions", "ID extraction OCR requires Premium or Enterprise"),
    _deny("/v1/id-extractions/*", "ID extraction OCR requires Premium or Enterprise"),
    _deny(
        "/v1/visitors/verify/id-scan",
        "ID scan verification requires Premium or Enterprise",
    ),
    _deny(
        "/v1/visitors/sessions/*/apply-id-scan",
        "Applying ID scan results requires Premium or Enterprise",
    ),
    # Mint a signed registration QR for visitor self-service. Free
    # tenants fall back to the public kiosk URL (/register/{tenant_id})
    # which works on every plan and needs no token.
    _deny(
        "/v1/visitors/registration-qr",
        "Mint a signed registration QR for visitor self-service",
        methods=["POST"],
    ),
    _deny(
        "/v1/checkout/sessions",
        "Advanced checkout sessions require Premium or Enterprise",
    ),
    _deny(
        "/v1/checkout/sessions/*",
        "Advanced checkout sessions require Premium or Enterprise",
    ),
    _deny(
        "/v1/notifications/test",
        "Test notifications require Premium or Enterprise",
        methods=["POST"],
    ),
    # ── Bulk operations — blocked entirely on Free ────────────────────
    _deny(
        "/v1/visitors/sessions/bulk/*",
        "Bulk visitor-session operations require Premium or Enterprise",
    ),
    _deny(
        "/v1/departments/bulk/*",
        "Bulk department operations require Premium or Enterprise",
    ),
    _deny(
        "/v1/support-cases/bulk/*",
        "Bulk support-case operations require Premium or Enterprise",
    ),
    # ── Write-method blocks (GET still works so kiosk / consent screen keep functioning) ──
    _deny(
        "/v1/checkin-configs",
        "Custom kiosk configurations require Premium or Enterprise",
        methods=["POST", "PATCH", "DELETE"],
    ),
    _deny(
        "/v1/checkin-configs/*",
        "Custom kiosk configurations require Premium or Enterprise",
        methods=["POST", "PATCH", "DELETE"],
    ),
    _deny(
        "/v1/privacy-notices",
        "Editing privacy notices requires Premium or Enterprise",
        methods=["POST", "PATCH"],
    ),
    _deny(
        "/v1/privacy-notices/*",
        "Editing privacy notices requires Premium or Enterprise",
        methods=["POST", "PATCH"],
    ),
    _deny(
        "/v1/me/saved-filters",
        "Saved filters require Premium or Enterprise",
        methods=["POST", "PATCH", "DELETE"],
    ),
    _deny(
        "/v1/me/saved-filters/*",
        "Saved filters require Premium or Enterprise",
        methods=["POST", "PATCH", "DELETE"],
    ),
    _deny(
        "/v1/me/column-prefs",
        "Persisting column preferences requires Premium or Enterprise",
        methods=["PUT"],
    ),
]

# Starter: same as Free but unlocks badge printing and QR check-in
# (which lives under /v1/checkin* — already permitted by default).
# Still no appointments, host roster, branding, KYC, multi-location, CSV
# export, host email.
STARTER_DENIED_FEATURES: List[FeatureRule] = [
    _deny("/v1/appointments*", "Appointments require Premium or Enterprise"),
    _deny("/v1/appointments/*", "Appointments require Premium or Enterprise"),
    _deny("/v1/hosts*", "Host management requires Premium or Enterprise"),
    _deny("/v1/hosts/*", "Host management requires Premium or Enterprise"),
    _deny("/v1/branding*", "Custom branding requires Premium or Enterprise"),
    _deny("/v1/branding/*", "Custom branding requires Premium or Enterprise"),
    _deny("/v1/kyc*", "ID verification requires Premium or Enterprise"),
    _deny("/v1/kyc/*", "ID verification requires Premium or Enterprise"),
    _deny(
        "/v1/dashboard/export*",
        "CSV export requires Premium or Enterprise",
        methods=["GET"],
    ),
    _deny(
        "/v1/compliance/export*",
        "CSV export requires Premium or Enterprise",
        methods=["GET"],
    ),
    _deny(
        "/v1/notifications/preferences",
        "Host email notifications require Premium or Enterprise",
        methods=["PUT", "PATCH"],
    ),
    _deny(
        "/v1/branches",
        "Multi-location requires Premium or Enterprise",
        methods=["POST"],
    ),
    _deny(
        "/v1/branches/*",
        "Multi-location requires Premium or Enterprise",
        methods=["POST", "PUT", "PATCH", "DELETE"],
    ),
]

# Premium: only locks out enterprise-only features (SSO, advanced
# integrations, watchlist). API access is an add-on but baked-in here.
# We also carry an explicit allow rule for ``/v1/kyc/*`` so the plan
# data is self-documenting — ``services.kyc_service`` decides KYC
# availability from these rules (deny-list semantics, same as the
# middleware), so the allow rule is documentation, not a gate.
PREMIUM_DENIED_FEATURES: List[FeatureRule] = [
    # Watchlist / flagged visitors — Enterprise only
    _deny("/v1/watchlist*", "Watchlist requires Enterprise"),
    _deny("/v1/watchlist/*", "Watchlist requires Enterprise"),
    # SSO — Enterprise only
    _deny("/v1/sso*", "SSO requires Enterprise"),
    _deny("/v1/sso/*", "SSO requires Enterprise"),
    # ID verification (Dojah KYC) — included in Premium
    _allow("/v1/kyc/*", "ID verification included"),
]

# Enterprise: no denies — everything available. The explicit KYC allow
# rule mirrors Premium so the catalogue payload makes the entitlement
# obvious to the frontend / admin UI.
ENTERPRISE_DENIED_FEATURES: List[FeatureRule] = [
    _allow("/v1/kyc/*", "ID verification included"),
]


@dataclass(frozen=True)
class CanonicalPlan:
    """Frozen tier definition used to upsert canonical plans on startup."""

    name: str
    display_name: str
    tier: PlanTier
    description: str
    base_price_monthly: float
    base_price_yearly: float
    feature_rules: List[FeatureRule]
    tenant_caps: TenantCapLimit
    storage_limits: StorageLimit
    crud_limits: List[CrudLimit] = field(default_factory=list)
    retrieval_quotas: List[RetrievalQuota] = field(default_factory=list)
    priority_support: bool = False
    sla_response_hours: Optional[int] = None
    custom_branding: bool = False
    api_access: bool = False
    support_tier: SupportTier = SupportTier.NONE
    is_public: bool = True
    sort_order: int = 0
    # Free-trial length in days. 0 = the plan offers no trial (Free,
    # Enterprise). Paid singletons (Starter, Premium) set this > 0 so
    # tenants can claim a one-time trial via POST /v1/trials/claim.
    trial_days: int = 0

    # Subset of plan / tenant_cap field names an application admin may
    # change via PUT /v1/plans/{id}. Anything outside this set is
    # locked at the tier level.
    adjustable_plan_fields: FrozenSet[str] = frozenset()
    adjustable_cap_fields: FrozenSet[str] = frozenset()


# ── Free ────────────────────────────────────────────────────────────────
FREE_PLAN = CanonicalPlan(
    name=FREE_PLAN_NAME,
    display_name="Free",
    tier=PlanTier.FREE,
    description=(
        "For trying things out. Best for testing before switching from paper. "
        "Manual check-in and check-out, searchable visitor logs, encrypted database."
    ),
    base_price_monthly=0.0,
    base_price_yearly=0.0,
    feature_rules=FREE_DENIED_FEATURES,
    tenant_caps=TenantCapLimit(
        max_system_users=1,
        max_departments=1,
        max_branches=1,
        max_visitors_per_month=50,
        max_appointments_per_month=0,
    ),
    storage_limits=StorageLimit(
        max_documents=10, max_storage_mb=10, max_file_size_mb=2
    ),
    # Daily write throttle on Free — caps the burst rate that the
    # monthly tenant_caps don't. Reset interval is DAILY so the counter
    # rolls over at 00:00 UTC.
    crud_limits=[
        CrudLimit(
            collection="visitors",
            max_create=5,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 5 visitor records per day",
        ),
        CrudLimit(
            collection="visitor_profiles",
            max_update=10,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 10 visitor-profile edits per day",
        ),
        CrudLimit(
            collection="documents",
            max_create=2,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 2 document uploads per day",
        ),
    ],
    # Daily read quotas — generous enough that a normal day of usage
    # doesn't hit them (HTTP cache hits still count against the cap, so
    # tight values would lock people out on dashboard refresh).
    retrieval_quotas=[
        RetrievalQuota(
            collection="dashboard",
            max_reads=200,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 200 dashboard reads per day",
        ),
        RetrievalQuota(
            collection="visitors",
            max_reads=200,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 200 visitor reads per day",
        ),
        RetrievalQuota(
            collection="visit_sessions",
            max_reads=300,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 300 visit-session reads per day",
        ),
        RetrievalQuota(
            collection="documents",
            max_reads=50,
            reset_interval=QuotaResetInterval.DAILY,
            description="Free plan: 50 document reads per day",
        ),
    ],
    priority_support=False,
    custom_branding=False,
    api_access=False,
    support_tier=SupportTier.NONE,
    sort_order=10,
    # Only the visitors-per-month cap is admin-adjustable on Free.
    adjustable_cap_fields=frozenset({"max_visitors_per_month"}),
)

# ── Starter ─────────────────────────────────────────────────────────────
STARTER_PLAN = CanonicalPlan(
    name=STARTER_PLAN_NAME,
    display_name="Starter",
    tier=PlanTier.STARTER,
    description=(
        "For small offices ready to get organized. Replace your paper logbook "
        "with a simple digital system. QR check-in, manual checkout, badge printing, "
        "visitor purpose tracking."
    ),
    base_price_monthly=35_000.0,
    base_price_yearly=35_000.0 * 12,
    feature_rules=STARTER_DENIED_FEATURES,
    tenant_caps=TenantCapLimit(
        max_system_users=5,
        max_departments=3,
        max_branches=1,
        max_visitors_per_month=150,
        max_appointments_per_month=0,
    ),
    storage_limits=StorageLimit(
        max_documents=500, max_storage_mb=512, max_file_size_mb=5
    ),
    priority_support=False,
    custom_branding=False,
    api_access=False,
    support_tier=SupportTier.STANDARD,
    sort_order=20,
    trial_days=PAID_PLAN_TRIAL_DAYS,
    adjustable_cap_fields=frozenset(
        {"max_visitors_per_month", "max_departments", "max_system_users"}
    ),
)

# ── Premium ─────────────────────────────────────────────────────────────
PREMIUM_PLAN = CanonicalPlan(
    name=PREMIUM_PLAN_NAME,
    display_name="Premium",
    tier=PlanTier.PREMIUM,
    description=(
        "For growing teams that need speed plus automation. Multi-location, "
        "QR self check-in/out, repeat visitor recognition, appointments, custom "
        "branding, host email notifications, CSV export, ID verification, API access."
    ),
    # Per-location pricing is documented in the frontend guide; the
    # base price here is for a single location and the checkout layer
    # multiplies by the requested location count.
    base_price_monthly=150_000.0,
    base_price_yearly=150_000.0 * 12,
    feature_rules=PREMIUM_DENIED_FEATURES,
    tenant_caps=TenantCapLimit(
        max_system_users=50,
        max_departments=15,  # per location — admins can scale via overrides
        # Task 9: per-location pricing flip. Premium tenants now pay per
        # branch via the "additional-branch" add-on (see addon_bootstrap.py)
        # rather than getting unlimited branches baked into the tier. Note:
        # this canonical-config value alone does NOT change the STORED
        # plan document for existing installs — plan_bootstrap's refresh
        # deliberately excludes tenant_caps (see
        # services.plan_bootstrap._canonical_to_plan_update) so an admin's
        # tuned caps survive redeploys. The stored doc is flipped by the
        # one-shot services.premium_max_branches_flip_migration, which MUST
        # run after the services.premium_branch_grandfather_backfill grants
        # existing tenants their perpetual per-branch entitlements.
        max_branches=1,
        max_visitors_per_month=500,  # per location baseline
        max_appointments_per_month=None,
        visitors_per_branch_per_month=1000,
    ),
    storage_limits=StorageLimit(
        max_documents=10_000, max_storage_mb=10_240, max_file_size_mb=20
    ),
    priority_support=True,
    sla_response_hours=24,
    custom_branding=True,
    api_access=True,
    support_tier=SupportTier.STANDARD,
    sort_order=30,
    trial_days=PAID_PLAN_TRIAL_DAYS,
    adjustable_cap_fields=frozenset(
        {
            "max_visitors_per_month",
            "max_departments",
            "max_system_users",
            "max_branches",
            "max_appointments_per_month",
            "visitors_per_branch_per_month",
        }
    ),
    adjustable_plan_fields=frozenset(
        {"base_price_monthly", "base_price_yearly", "sla_response_hours"}
    ),
)

# ── Enterprise template ─────────────────────────────────────────────────
# This is the *template* used to seed sensible defaults when a sales /
# admin user creates a new bespoke enterprise plan. It is NOT bootstrapped
# into MongoDB on startup — there can be many enterprise plans and each
# one is keyed by its own unique slug (e.g. ``"enterprise-acme"``).
#
# When the admin wires up a new enterprise plan they pick the slug,
# starting from these defaults, and optionally register a FastAPI
# sub-app of custom endpoints under ``/v1/enterprise/<slug>/*`` via
# ``core.enterprise_apps.register_enterprise_app``.
ENTERPRISE_TEMPLATE = CanonicalPlan(
    name=ENTERPRISE_TEMPLATE_NAME,
    display_name="Enterprise",
    tier=PlanTier.ENTERPRISE,
    description=(
        "For organizations that need full control, compliance and integrations. "
        "Custom forms, liveness check, watchlist, SSO (Azure / Google Workspace), "
        "private cloud or on-prem deployment, dedicated SLA. Custom pricing."
    ),
    base_price_monthly=0.0,  # custom — quoted via sales
    base_price_yearly=0.0,
    feature_rules=ENTERPRISE_DENIED_FEATURES,
    tenant_caps=TenantCapLimit(
        max_system_users=None,
        max_departments=None,
        max_branches=None,
        max_visitors_per_month=None,
        max_appointments_per_month=None,
    ),
    storage_limits=StorageLimit(
        max_documents=None, max_storage_mb=None, max_file_size_mb=50
    ),
    priority_support=True,
    sla_response_hours=4,
    custom_branding=True,
    api_access=True,
    support_tier=SupportTier.PRIORITY,
    sort_order=40,
    # Enterprise plans are bespoke — admins can edit anything.
    adjustable_cap_fields=frozenset(
        {
            "max_visitors_per_month",
            "max_departments",
            "max_system_users",
            "max_branches",
            "max_appointments_per_month",
            "visitors_per_branch_per_month",
        }
    ),
    adjustable_plan_fields=frozenset(
        {
            "base_price_monthly",
            "base_price_yearly",
            "sla_response_hours",
            "support_tier",
            "custom_branding",
            "api_access",
            "priority_support",
        }
    ),
)


# Canonical singleton plans — these get auto-upserted on every startup.
# Enterprise plans are NOT in this dict because there can be many of them.
CANONICAL_PLANS: Dict[str, CanonicalPlan] = {
    FREE_PLAN_NAME: FREE_PLAN,
    STARTER_PLAN_NAME: STARTER_PLAN,
    PREMIUM_PLAN_NAME: PREMIUM_PLAN,
}


def get_canonical_plan(name: str) -> Optional[CanonicalPlan]:
    """Look up a singleton canonical plan by slug. Returns None otherwise.

    Enterprise plans are not in the canonical set (each one is bespoke);
    callers that need enterprise editability rules should fall back to
    ``ENTERPRISE_TEMPLATE`` for any plan with ``tier = PlanTier.ENTERPRISE``.
    """
    return CANONICAL_PLANS.get(name)


def is_canonical_plan_name(name: str) -> bool:
    """Backwards-compatible alias for ``is_singleton_plan_name``."""
    return name in CANONICAL_PLANS


def is_singleton_plan_name(name: str) -> bool:
    """True only for the three singleton plan slugs (free/starter/premium).

    Singletons cannot be archived or deleted (it would break tenants on
    the free fallback) and only one of each can ever be created.
    """
    return name in CANONICAL_PLANS
