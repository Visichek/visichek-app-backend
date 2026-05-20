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


TOGGLEABLE_FEATURES: dict[str, FeatureToggleSpec] = {
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
    ),
}


def get_feature_catalog() -> list[FeatureToggleSpec]:
    """Stable-ordered list of every togglable feature."""
    return list(TOGGLEABLE_FEATURES.values())


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
