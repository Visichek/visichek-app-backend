"""
Unit tests for the PlanEnforcementMiddleware logic.
Tests feature gating, quota checking, and path extraction.
"""

from __future__ import annotations


from config.plan_tiers import (
    ENTERPRISE_TEMPLATE,
    FREE_PLAN,
    PREMIUM_PLAN,
    STARTER_PLAN,
)
from core.plan_enforcement import (
    _extract_collection_from_path,
    _is_exempt_path,
    _check_feature_access,
)


def _rules_as_dicts(plan) -> list[dict]:
    """Serialise a canonical plan's FeatureRule list into the dict shape
    ``_check_feature_access`` consumes (it reads the rules off the cached
    plan payload, which is dict-shaped, not Pydantic models)."""
    return [rule.model_dump() for rule in plan.feature_rules]


class TestPathExtraction:
    def test_extract_visitors(self):
        assert _extract_collection_from_path("/v1/visitors/abc123") == "visitors"

    def test_extract_appointments(self):
        assert _extract_collection_from_path("/v1/appointments") == "appointments"

    def test_extract_system_users(self):
        assert (
            _extract_collection_from_path("/v1/system-users/signup") == "system_users"
        )

    def test_extract_dashboard(self):
        assert _extract_collection_from_path("/v1/dashboard/stats") == "dashboard"

    def test_extract_audit(self):
        assert _extract_collection_from_path("/v1/audit/logs") == "audit"

    def test_extract_departments(self):
        assert _extract_collection_from_path("/v1/departments") == "departments"

    def test_extract_unknown_returns_none(self):
        assert _extract_collection_from_path("/v1/unknown-resource") is None

    def test_extract_root_returns_none(self):
        assert _extract_collection_from_path("/") is None

    def test_extract_no_version_prefix(self):
        assert _extract_collection_from_path("/visitors/abc") is None


class TestExemptPaths:
    def test_root_is_exempt(self):
        assert _is_exempt_path("/") is True

    def test_health_is_exempt(self):
        assert _is_exempt_path("/health") is True

    def test_docs_is_exempt(self):
        assert _is_exempt_path("/docs") is True

    def test_admin_paths_exempt(self):
        assert _is_exempt_path("/v1/admins/login") is True
        assert _is_exempt_path("/v1/admins/tenants/bootstrap") is True

    def test_plan_management_exempt(self):
        assert _is_exempt_path("/v1/plans") is True
        assert _is_exempt_path("/v1/plans/plan_123") is True

    def test_subscription_management_exempt(self):
        assert _is_exempt_path("/v1/subscriptions") is True

    def test_visitor_not_exempt(self):
        assert _is_exempt_path("/v1/visitors/abc") is False

    def test_departments_not_exempt(self):
        assert _is_exempt_path("/v1/departments") is False


class TestFeatureAccess:
    def test_no_rules_allows_all(self):
        allowed, reason = _check_feature_access("/v1/visitors", "POST", [])
        assert allowed is True
        assert reason is None

    def test_enabled_rule_allows(self):
        rules = [
            {
                "endpoint_pattern": "/v1/visitors/*",
                "methods": ["GET", "POST"],
                "enabled": True,
            },
        ]
        allowed, reason = _check_feature_access("/v1/visitors/abc", "GET", rules)
        assert allowed is True

    def test_disabled_rule_blocks(self):
        rules = [
            {
                "endpoint_pattern": "/v1/audit/*",
                "methods": ["GET", "POST", "PUT", "DELETE"],
                "enabled": False,
                "description": "Audit not available on free plan",
            },
        ]
        allowed, reason = _check_feature_access("/v1/audit/logs", "GET", rules)
        assert allowed is False
        assert "Audit not available" in reason

    def test_method_mismatch_allows(self):
        rules = [
            {
                "endpoint_pattern": "/v1/visitors/*",
                "methods": ["POST"],
                "enabled": False,
            },
        ]
        # GET is not in the disabled methods list
        allowed, reason = _check_feature_access("/v1/visitors/abc", "GET", rules)
        assert allowed is True

    def test_pattern_mismatch_allows(self):
        rules = [
            {"endpoint_pattern": "/v1/audit/*", "methods": ["GET"], "enabled": False},
        ]
        # Different path entirely
        allowed, reason = _check_feature_access("/v1/visitors/abc", "GET", rules)
        assert allowed is True

    def test_multiple_rules_first_match_wins(self):
        rules = [
            {
                "endpoint_pattern": "/v1/visitors/*",
                "methods": ["GET"],
                "enabled": False,
                "description": "Blocked",
            },
            {"endpoint_pattern": "/v1/*", "methods": ["GET"], "enabled": True},
        ]
        # First matching rule blocks
        allowed, reason = _check_feature_access("/v1/visitors/abc", "GET", rules)
        assert allowed is False

    def test_wildcard_pattern(self):
        rules = [
            {
                "endpoint_pattern": "/v1/*",
                "methods": ["DELETE"],
                "enabled": False,
                "description": "No deletes",
            },
        ]
        allowed, reason = _check_feature_access("/v1/departments/abc", "DELETE", rules)
        assert allowed is False


class TestPlanCacheMerge:
    def test_merge_feature_rules_no_overrides(self):
        from services.plan_cache_service import _merge_feature_rules

        rules = [{"endpoint_pattern": "/v1/visitors/*", "enabled": True}]
        merged = _merge_feature_rules(rules, None)
        assert merged == rules

    def test_merge_feature_rules_with_override(self):
        from services.plan_cache_service import _merge_feature_rules

        rules = [{"endpoint_pattern": "/v1/audit/*", "enabled": False}]
        overrides = {"/v1/audit/*": {"enabled": True}}
        merged = _merge_feature_rules(rules, overrides)
        assert merged[0]["enabled"] is True

    def test_merge_crud_limits_no_overrides(self):
        from services.plan_cache_service import _merge_crud_limits

        limits = [{"collection": "visitors", "max_create": 100}]
        merged = _merge_crud_limits(limits, None)
        assert merged == limits

    def test_merge_crud_limits_with_override(self):
        from services.plan_cache_service import _merge_crud_limits

        limits = [{"collection": "visitors", "max_create": 100}]
        overrides = {"visitors": {"max_create": 999}}
        merged = _merge_crud_limits(limits, overrides)
        assert merged[0]["max_create"] == 999

    def test_merge_tenant_caps_no_overrides(self):
        from services.plan_cache_service import _merge_tenant_caps

        caps = {"max_system_users": 10}
        merged = _merge_tenant_caps(caps, None)
        assert merged == caps

    def test_merge_tenant_caps_with_override(self):
        from services.plan_cache_service import _merge_tenant_caps

        caps = {"max_system_users": 10, "max_departments": 5}
        overrides = {"max_system_users": 50}
        merged = _merge_tenant_caps(caps, overrides)
        assert merged["max_system_users"] == 50
        assert merged["max_departments"] == 5


class TestCanonicalTierHostGating:
    """Regression guard for the host roster being Premium+ only.

    Hosts are denied on Free and Starter and allowed on Premium/Enterprise.
    These assert the gate against the *actual* canonical plan feature rules
    (via the same ``_check_feature_access`` the middleware runs), so a future
    edit to the deny lists can't silently re-open the host roster on a
    paid-but-not-Premium tier without turning this suite red.
    """

    def test_free_denies_hosts_collection(self):
        allowed, reason = _check_feature_access(
            "/v1/hosts", "GET", _rules_as_dicts(FREE_PLAN)
        )
        assert allowed is False
        assert reason is not None and "Premium" in reason

    def test_free_denies_host_detail_write(self):
        allowed, _ = _check_feature_access(
            "/v1/hosts/abc123", "POST", _rules_as_dicts(FREE_PLAN)
        )
        assert allowed is False

    def test_starter_denies_hosts_collection(self):
        allowed, reason = _check_feature_access(
            "/v1/hosts", "GET", _rules_as_dicts(STARTER_PLAN)
        )
        assert allowed is False
        assert reason is not None and "Premium" in reason

    def test_starter_denies_host_detail_delete(self):
        allowed, _ = _check_feature_access(
            "/v1/hosts/abc123", "DELETE", _rules_as_dicts(STARTER_PLAN)
        )
        assert allowed is False

    def test_premium_allows_hosts_all_methods(self):
        rules = _rules_as_dicts(PREMIUM_PLAN)
        for method in ("GET", "POST", "PATCH", "DELETE"):
            allowed, _ = _check_feature_access("/v1/hosts", method, rules)
            assert allowed is True, f"Premium must allow {method} /v1/hosts"
        allowed, _ = _check_feature_access("/v1/hosts/abc123", "GET", rules)
        assert allowed is True

    def test_enterprise_allows_hosts(self):
        allowed, _ = _check_feature_access(
            "/v1/hosts", "POST", _rules_as_dicts(ENTERPRISE_TEMPLATE)
        )
        assert allowed is True

    def test_starter_still_allows_walk_in_checkin(self):
        # Sanity: gating hosts must NOT have caught the walk-in check-in
        # flow, which Starter is supposed to keep.
        allowed, _ = _check_feature_access(
            "/v1/checkins", "POST", _rules_as_dicts(STARTER_PLAN)
        )
        assert allowed is True


class TestFeatureKeyMapSync:
    """Guard against drift between the per-tier deny lists in
    ``config/plan_tiers.py`` and the endpoint→feature-key map in
    ``services/me_limitations_service.py``.

    The two are hand-synced (the map's own comment says so), and the
    limitations payload that drives frontend nav-hiding depends on every
    mapped pattern actually being a rule some tier ships. A typo or a
    removed deny rule would silently stop the matching nav item from
    hiding — exactly the bug this catches.
    """

    @staticmethod
    def _all_rule_patterns() -> set[str]:
        return {
            rule.endpoint_pattern
            for plan in (FREE_PLAN, STARTER_PLAN, PREMIUM_PLAN, ENTERPRISE_TEMPLATE)
            for rule in plan.feature_rules
        }

    def test_every_mapped_pattern_is_a_real_rule(self):
        from services.me_limitations_service import _ENDPOINT_TO_FEATURE_KEY

        known = self._all_rule_patterns()
        orphans = [p for p in _ENDPOINT_TO_FEATURE_KEY if p not in known]
        assert not orphans, (
            "_ENDPOINT_TO_FEATURE_KEY references endpoint patterns that no "
            f"plan tier defines (drift): {orphans}"
        )

    def test_hosts_patterns_map_to_hosts_feature_key(self):
        from services.me_limitations_service import _ENDPOINT_TO_FEATURE_KEY

        assert _ENDPOINT_TO_FEATURE_KEY.get("/v1/hosts*") == "hosts"
        assert _ENDPOINT_TO_FEATURE_KEY.get("/v1/hosts/*") == "hosts"
