"""Plan-feature toggle service.

A small registry of "togglable features" so the frontend can present a
checklist UI on each plan instead of hand-editing the raw
``feature_rules`` array. Each entry maps a stable ``feature_key`` (e.g.
``kyc``) to its canonical endpoint pattern, methods, and the user-
facing label / description shown in the plans page.

To add a new feature:

1. Append to :data:`TOGGLEABLE_FEATURES` below with a stable key.
2. Document it in ``backend-docs/plan-feature-addition-page.md`` so the
   frontend knows what to render.

Adding a feature here does NOT auto-enable it on existing plans —
super_admins / app admins toggle it via
``POST /v1/plans/{plan_id}/features/{feature_key}``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode, resource_not_found
from schemas.plan_schema import FeatureRule, PlanOut, PlanUpdate
from services.audit_service import record_audit_event
from services.plan_service import retrieve_plan_by_id, update_plan_by_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureToggleSpec:
    """One entry in the togglable-feature catalog."""

    key: str
    label: str
    description: str
    endpoint_pattern: str
    methods: tuple[str, ...]
    # ``default_enabled`` is the value applied when a plan first turns
    # the feature on and we have nothing existing to merge against. The
    # frontend can render the catalog from this without a second call.
    default_enabled: bool = True
    # When ``True`` the feature only makes sense if env-level
    # configuration is also present (e.g. KYC needs DOJAH_APP_ID).
    # The frontend can dim the toggle or surface a banner.
    requires_external_config: bool = False
    # Optional human-readable hint about what to set in the env when
    # ``requires_external_config`` is ``True``.
    external_config_hint: Optional[str] = None
    # Full set of deny rules this feature compiles to when disabled.
    # ``endpoint_pattern``/``methods`` above stay the *primary* pattern
    # used by ``set_plan_feature`` (single-rule toggle, unchanged
    # behavior); ``deny_rules`` is the complete list the enterprise
    # composer's compiler (``compile_feature_rules``) emits — copied
    # verbatim from the proven per-tier deny lists in
    # ``config.plan_tiers`` so we never invent a pattern that isn't
    # already enforced elsewhere. Empty for features with no existing
    # endpoint-level gate (e.g. ``api_access``, which is a boolean plan
    # flag today, not an fnmatch-gated route).
    deny_rules: tuple[FeatureRule, ...] = ()


TOGGLEABLE_FEATURES: dict[str, FeatureToggleSpec] = {
    "branding": FeatureToggleSpec(
        key="branding",
        label="Custom branding",
        description="Custom logos, colors, and badge styling.",
        endpoint_pattern="/v1/branding*",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/branding*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Custom branding requires Premium or Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/branding/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Custom branding requires Premium or Enterprise",
            ),
        ),
    ),
    "branching": FeatureToggleSpec(
        key="branching",
        label="Multi-location (branches)",
        description="Create and manage more than one branch/location.",
        endpoint_pattern="/v1/branches/*",
        methods=("POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/branches",
                methods=["POST"],
                enabled=False,
                description="Multi-location requires Premium or Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/branches/*",
                methods=["POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Multi-location requires Premium or Enterprise",
            ),
        ),
    ),
    "appointments": FeatureToggleSpec(
        key="appointments",
        label="Appointments",
        description="Appointment scheduling + host roster.",
        endpoint_pattern="/v1/appointments*",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/appointments*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Appointments require Premium or Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/appointments/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Appointments require Premium or Enterprise",
            ),
        ),
    ),
    "badges": FeatureToggleSpec(
        key="badges",
        label="Badge printing",
        description="Visitor badge printing.",
        endpoint_pattern="/v1/badges*",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/badges*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Badge printing requires Starter or higher",
            ),
            FeatureRule(
                endpoint_pattern="/v1/badges/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Badge printing requires Starter or higher",
            ),
        ),
    ),
    "csv_export": FeatureToggleSpec(
        key="csv_export",
        label="CSV export",
        description="CSV export of dashboard and compliance data.",
        endpoint_pattern="/v1/dashboard/export*",
        methods=("GET",),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/dashboard/export*",
                methods=["GET"],
                enabled=False,
                description="CSV export requires Premium or Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/compliance/export*",
                methods=["GET"],
                enabled=False,
                description="CSV export requires Premium or Enterprise",
            ),
        ),
    ),
    "host_email_notifications": FeatureToggleSpec(
        key="host_email_notifications",
        label="Host email notifications",
        description="Email notifications to hosts on visitor arrival.",
        endpoint_pattern="/v1/notifications/preferences",
        methods=("PUT", "PATCH"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/notifications/preferences",
                methods=["PUT", "PATCH"],
                enabled=False,
                description="Host email notifications require Premium or Enterprise",
            ),
        ),
    ),
    "kyc": FeatureToggleSpec(
        key="kyc",
        label="Identity verification (Dojah KYC)",
        description=(
            "Adds the Dojah KYC step to the kiosk submission flow. "
            "Visitors land in pending_verification while the widget "
            "runs; on success they move to pending_approval with "
            "verified=true. When disabled, the kiosk skips the "
            "verification step entirely."
        ),
        endpoint_pattern="/v1/kyc/*",
        methods=("GET", "POST"),
        default_enabled=True,
        requires_external_config=True,
        external_config_hint=(
            "Set DOJAH_APP_ID, DOJAH_SECRET_KEY, DOJAH_PUBLIC_KEY in "
            "the backend environment for the toggle to take effect."
        ),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/kyc*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="ID verification requires Premium or Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/kyc/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="ID verification requires Premium or Enterprise",
            ),
        ),
    ),
    "watchlist": FeatureToggleSpec(
        key="watchlist",
        label="Watchlist",
        description="Flagged / watchlisted visitors.",
        endpoint_pattern="/v1/watchlist*",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/watchlist*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Watchlist requires Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/watchlist/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="Watchlist requires Enterprise",
            ),
        ),
    ),
    "sso": FeatureToggleSpec(
        key="sso",
        label="SSO",
        description="Single sign-on (Azure / Google Workspace).",
        endpoint_pattern="/v1/sso*",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(
            FeatureRule(
                endpoint_pattern="/v1/sso*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="SSO requires Enterprise",
            ),
            FeatureRule(
                endpoint_pattern="/v1/sso/*",
                methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                enabled=False,
                description="SSO requires Enterprise",
            ),
        ),
    ),
    # ``api_access`` has no endpoint-level deny pattern anywhere in the
    # codebase today — it's a plain boolean flag on the plan document
    # (``CanonicalPlan.api_access`` / ``PlanBase.api_access``), not an
    # fnmatch-gated route surface. Listed here so the enterprise
    # composer's checklist UI can toggle it (it flips the boolean via
    # the normal ``PlanUpdate.api_access`` field, not via feature_rules
    # — ``deny_rules`` stays empty rather than inventing a pattern that
    # nothing enforces).
    "api_access": FeatureToggleSpec(
        key="api_access",
        label="API access",
        description="Programmatic API access for this plan.",
        endpoint_pattern="/v1/api-access",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        deny_rules=(),
    ),
}


def get_feature_catalog() -> list[FeatureToggleSpec]:
    """Stable-ordered list of every togglable feature."""
    return list(TOGGLEABLE_FEATURES.values())


def compile_feature_rules(enabled: dict[str, bool]) -> list[FeatureRule]:
    """Compile a ``{feature_key: enabled}`` toggle map into ``feature_rules``.

    Used by the enterprise plan composer (and its preview endpoint) to
    turn a checklist of feature toggles into the same ``feature_rules``
    shape ``PlanEnforcementMiddleware`` already knows how to enforce —
    no new enforcement path, just a new way to author the list. Every
    deny rule emitted here is copied verbatim from
    :data:`TOGGLEABLE_FEATURES[key].deny_rules`, which in turn mirror
    the proven per-tier deny lists in ``config.plan_tiers``.

    A feature key absent from ``enabled`` falls back to its
    ``default_enabled`` value. Enabled features emit no rule (the
    middleware's default is "allowed"); disabled features emit their
    full ``deny_rules`` set. Unknown keys in ``enabled`` are ignored.
    """
    rules: list[FeatureRule] = []
    for key, spec in TOGGLEABLE_FEATURES.items():
        is_enabled = enabled.get(key, spec.default_enabled)
        if is_enabled:
            continue
        rules.extend(spec.deny_rules)
    return rules


def _spec_or_404(feature_key: str) -> FeatureToggleSpec:
    spec = TOGGLEABLE_FEATURES.get(feature_key)
    if spec is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Unknown feature_key '{feature_key}'",
            details={"available": sorted(TOGGLEABLE_FEATURES.keys())},
        )
    return spec


def _is_match(rule: FeatureRule, spec: FeatureToggleSpec) -> bool:
    """A feature_rule belongs to ``spec`` when both pattern and methods match."""
    if rule.endpoint_pattern != spec.endpoint_pattern:
        return False
    # Methods are an unordered set per Dojah's pattern. A rule with the
    # same pattern but a different method list is treated as unrelated
    # (it expresses a finer-grained intent that we don't want to
    # clobber).
    return set(m.upper() for m in rule.methods) == set(m.upper() for m in spec.methods)


async def set_plan_feature(
    *,
    plan_id: str,
    feature_key: str,
    enabled: bool,
    actor_id: str,
    actor_role: str,
    request_id: Optional[str] = None,
) -> PlanOut:
    """Enable or disable one feature_rule on a plan.

    The rule is upserted in-place: if a matching rule exists we flip
    its ``enabled`` flag (preserving any custom description); otherwise
    we append a new rule from the spec. Plan cache invalidation is
    handled by the queued writer that calls this.
    """
    if not ObjectId.is_valid(plan_id):
        raise resource_not_found(resource="Plan", resource_id=plan_id)

    spec = _spec_or_404(feature_key)

    plan = await retrieve_plan_by_id(plan_id)
    if not plan:
        raise resource_not_found(resource="Plan", resource_id=plan_id)

    new_rules: list[FeatureRule] = []
    matched = False
    previous_enabled: Optional[bool] = None
    for rule in plan.feature_rules:
        if _is_match(rule, spec):
            matched = True
            previous_enabled = rule.enabled
            if rule.enabled == enabled:
                # No-op — keep the rule as-is so we don't churn
                # ``description`` / ``methods`` if the admin had
                # customised them.
                new_rules.append(rule)
            else:
                new_rules.append(
                    FeatureRule(
                        endpoint_pattern=rule.endpoint_pattern,
                        methods=list(rule.methods),
                        enabled=enabled,
                        description=rule.description or spec.description,
                    )
                )
        else:
            new_rules.append(rule)

    if not matched:
        new_rules.append(
            FeatureRule(
                endpoint_pattern=spec.endpoint_pattern,
                methods=list(spec.methods),
                enabled=enabled,
                description=spec.description,
            )
        )

    if matched and previous_enabled == enabled:
        # Nothing actually changed — return the current plan without
        # writing or auditing.
        return plan

    updated = await update_plan_by_id(plan_id, PlanUpdate(feature_rules=new_rules))
    if updated is None:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Plan update raced with another writer — please retry",
        )

    await record_audit_event(
        actor_id=actor_id,
        actor_role=actor_role,
        action=f"plan.feature.{'enabled' if enabled else 'disabled'}",
        resource_type="plan",
        resource_id=plan_id,
        details={
            "feature_key": feature_key,
            "endpoint_pattern": spec.endpoint_pattern,
            "previous_enabled": previous_enabled,
            "enabled": enabled,
        },
        request_id=request_id,
    )
    return updated
