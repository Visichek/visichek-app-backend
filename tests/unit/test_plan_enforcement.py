"""
Unit tests for the PlanEnforcementMiddleware logic.
Tests feature gating, quota checking, and path extraction.
"""
from __future__ import annotations


from core.plan_enforcement import (
    _extract_collection_from_path,
    _is_exempt_path,
    _check_feature_access,
)


class TestPathExtraction:

    def test_extract_visitors(self):
        assert _extract_collection_from_path("/v1/visitors/abc123") == "visitors"

    def test_extract_appointments(self):
        assert _extract_collection_from_path("/v1/appointments") == "appointments"

    def test_extract_system_users(self):
        assert _extract_collection_from_path("/v1/system-users/signup") == "system_users"

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
        assert _is_exempt_path("/v1/plans/") is True
        assert _is_exempt_path("/v1/plans/plan_123") is True

    def test_subscription_management_exempt(self):
        assert _is_exempt_path("/v1/subscriptions/") is True

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
            {"endpoint_pattern": "/v1/visitors/*", "methods": ["GET", "POST"], "enabled": True},
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
            {"endpoint_pattern": "/v1/visitors/*", "methods": ["POST"], "enabled": False},
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
            {"endpoint_pattern": "/v1/visitors/*", "methods": ["GET"], "enabled": False, "description": "Blocked"},
            {"endpoint_pattern": "/v1/*", "methods": ["GET"], "enabled": True},
        ]
        # First matching rule blocks
        allowed, reason = _check_feature_access("/v1/visitors/abc", "GET", rules)
        assert allowed is False

    def test_wildcard_pattern(self):
        rules = [
            {"endpoint_pattern": "/v1/*", "methods": ["DELETE"], "enabled": False, "description": "No deletes"},
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
