"""
Unit tests for plan, subscription, discount, and usage schemas.
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from schemas.plan_schema import (
    PlanCreate,
    PlanUpdate,
    PlanOut,
    PlanTier,
    PlanStatus,
    FeatureRule,
    CrudLimit,
    RetrievalQuota,
    StorageLimit,
    TenantCapLimit,
    QuotaResetInterval,
)
from schemas.subscription_schema import (
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionStatus,
    BillingCycle,
)
from schemas.discount_schema import (
    DiscountCreate,
    DiscountOut,
    DiscountType,
    DiscountScope,
)
from schemas.usage_schema import (
    UsageRecordCreate,
    UsageAggregateCreate,
    OperationType,
    TenantUsageSummary,
)


# ============================================================================
# Plan Schema Tests
# ============================================================================


class TestPlanSchema:
    def test_plan_create_minimal(self):
        plan = PlanCreate(name="free-tier", display_name="Free Tier")
        assert plan.name == "free-tier"
        assert plan.tier == PlanTier.FREE
        assert plan.status == PlanStatus.DRAFT
        assert plan.base_price_monthly == 0.0
        assert plan.date_created > 0

    def test_plan_create_full(self):
        plan = PlanCreate(
            name="enterprise-v1",
            display_name="Enterprise Plan",
            tier=PlanTier.ENTERPRISE,
            description="Full featured plan",
            status=PlanStatus.DRAFT,
            base_price_monthly=999.99,
            base_price_yearly=9999.99,
            currency="USD",
            feature_rules=[
                FeatureRule(endpoint_pattern="/v1/visitors/*", enabled=True),
                FeatureRule(endpoint_pattern="/v1/audit/*", enabled=False),
            ],
            crud_limits=[
                CrudLimit(collection="visitors", max_create=1000, max_update=500),
            ],
            retrieval_quotas=[
                RetrievalQuota(
                    collection="dashboard",
                    max_reads=100,
                    reset_interval=QuotaResetInterval.DAILY,
                ),
            ],
            storage_limits=StorageLimit(max_documents=5000, max_storage_mb=10240),
            tenant_caps=TenantCapLimit(max_system_users=50, max_departments=20),
            priority_support=True,
            sla_response_hours=4,
            custom_branding=True,
            api_access=True,
        )
        assert plan.tier == PlanTier.ENTERPRISE
        assert len(plan.feature_rules) == 2
        assert plan.feature_rules[1].enabled is False
        assert plan.crud_limits[0].max_create == 1000
        assert plan.storage_limits.max_storage_mb == 10240
        assert plan.tenant_caps.max_system_users == 50

    def test_plan_create_rejects_negative_price(self):
        with pytest.raises(ValidationError):
            PlanCreate(name="bad-plan", display_name="Bad", base_price_monthly=-10)

    def test_plan_create_rejects_invalid_name(self):
        with pytest.raises(ValidationError):
            PlanCreate(name="invalid name!!", display_name="Bad Name")

    def test_plan_update_all_optional(self):
        update = PlanUpdate()
        assert update.name is None
        assert update.tier is None
        assert update.last_updated > 0

    def test_plan_update_partial(self):
        update = PlanUpdate(
            base_price_monthly=49.99,
            status=PlanStatus.ACTIVE,
        )
        assert update.base_price_monthly == 49.99
        assert update.status == PlanStatus.ACTIVE
        assert update.name is None

    def test_plan_out_converts_objectid(self):
        from bson import ObjectId

        oid = ObjectId()
        plan = PlanOut(
            _id=oid,
            name="test",
            display_name="Test",
            date_created=int(time.time()),
        )
        assert plan.id == str(oid)

    def test_plan_out_from_dict(self):
        from bson import ObjectId

        oid = ObjectId()
        data = {
            "_id": oid,
            "name": "from-dict",
            "display_name": "From Dict",
            "tier": "free",
            "status": "draft",
            "base_price_monthly": 0,
            "base_price_yearly": 0,
            "currency": "NGN",
            "feature_rules": [],
            "crud_limits": [],
            "retrieval_quotas": [],
            "storage_limits": {},
            "tenant_caps": {},
            "priority_support": False,
            "custom_branding": False,
            "api_access": False,
            "is_public": True,
            "sort_order": 0,
            "date_created": int(time.time()),
            "last_updated": int(time.time()),
        }
        plan = PlanOut(**data)
        assert plan.id == str(oid)
        assert plan.name == "from-dict"


class TestFeatureRule:
    def test_feature_rule_defaults(self):
        rule = FeatureRule(endpoint_pattern="/v1/visitors/*")
        assert rule.enabled is True
        assert "GET" in rule.methods
        assert "POST" in rule.methods

    def test_feature_rule_disabled(self):
        rule = FeatureRule(
            endpoint_pattern="/v1/audit/*",
            methods=["GET"],
            enabled=False,
            description="Audit disabled on free plan",
        )
        assert rule.enabled is False
        assert rule.methods == ["GET"]


class TestCrudLimit:
    def test_crud_limit_defaults(self):
        limit = CrudLimit(collection="visitors")
        assert limit.max_create is None
        assert limit.reset_interval == QuotaResetInterval.MONTHLY

    def test_crud_limit_with_caps(self):
        limit = CrudLimit(
            collection="visitors",
            max_create=100,
            max_update=200,
            max_delete=50,
            reset_interval=QuotaResetInterval.DAILY,
        )
        assert limit.max_create == 100
        assert limit.max_delete == 50


# ============================================================================
# Subscription Schema Tests
# ============================================================================


class TestSubscriptionSchema:
    def test_subscription_create_minimal(self):
        sub = SubscriptionCreate(
            tenant_id="tenant123",
            plan_id="plan456",
        )
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.billing_cycle == BillingCycle.MONTHLY
        assert sub.effective_price == 0.0
        assert sub.date_created > 0

    def test_subscription_create_with_trial(self):
        now = int(time.time())
        sub = SubscriptionCreate(
            tenant_id="t1",
            plan_id="p1",
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=now + 86400 * 14,
        )
        assert sub.status == SubscriptionStatus.TRIALING
        assert sub.trial_ends_at > now

    def test_subscription_create_rejects_negative_price(self):
        with pytest.raises(ValidationError):
            SubscriptionCreate(
                tenant_id="t1",
                plan_id="p1",
                effective_price=-100,
            )

    def test_subscription_with_overrides(self):
        sub = SubscriptionCreate(
            tenant_id="t1",
            plan_id="p1",
            feature_overrides={"/v1/audit/*": {"enabled": True}},
            crud_limit_overrides={"visitors": {"max_create": 9999}},
            tenant_cap_overrides={"max_system_users": 100},
        )
        assert sub.feature_overrides["/v1/audit/*"]["enabled"] is True
        assert sub.crud_limit_overrides["visitors"]["max_create"] == 9999

    def test_subscription_out_converts_objectid(self):
        from bson import ObjectId

        oid = ObjectId()
        sub = SubscriptionOut(
            _id=oid,
            tenant_id="t1",
            plan_id="p1",
        )
        assert sub.id == str(oid)


# ============================================================================
# Discount Schema Tests
# ============================================================================


class TestDiscountSchema:
    def test_discount_create_percentage(self):
        discount = DiscountCreate(
            code="LAUNCH50",
            name="Launch Discount",
            discount_type=DiscountType.PERCENTAGE,
            value=50.0,
            scope=DiscountScope.GLOBAL,
        )
        assert discount.code == "LAUNCH50"
        assert discount.value == 50.0

    def test_discount_create_fixed(self):
        discount = DiscountCreate(
            code="SAVE100",
            name="Save 100",
            discount_type=DiscountType.FIXED,
            value=100.0,
            scope=DiscountScope.GLOBAL,
        )
        assert discount.value == 100.0

    def test_discount_rejects_percentage_over_100(self):
        with pytest.raises(ValidationError):
            DiscountCreate(
                code="BAD",
                name="Bad",
                discount_type=DiscountType.PERCENTAGE,
                value=150.0,
            )

    def test_discount_rejects_negative_fixed(self):
        with pytest.raises(ValidationError):
            DiscountCreate(
                code="BAD",
                name="Bad",
                discount_type=DiscountType.FIXED,
                value=-10.0,
            )

    def test_discount_tenant_scope_requires_target(self):
        with pytest.raises(ValidationError):
            DiscountCreate(
                code="TENANT_ONLY",
                name="Tenant Only",
                scope=DiscountScope.TENANT,
                value=10.0,
                # Missing target_tenant_id!
            )

    def test_discount_tenant_scope_with_target(self):
        discount = DiscountCreate(
            code="TENANT_DEAL",
            name="Tenant Deal",
            scope=DiscountScope.TENANT,
            value=20.0,
            target_tenant_id="tenant_abc",
        )
        assert discount.target_tenant_id == "tenant_abc"

    def test_discount_rejects_invalid_code(self):
        with pytest.raises(ValidationError):
            DiscountCreate(
                code="BAD CODE!!",
                name="Bad",
                value=10.0,
            )

    def test_discount_out_converts_objectid(self):
        from bson import ObjectId

        oid = ObjectId()
        discount = DiscountOut(
            _id=oid,
            code="TEST",
            name="Test",
            value=10.0,
        )
        assert discount.id == str(oid)


# ============================================================================
# Usage Schema Tests
# ============================================================================


class TestUsageSchema:
    def test_usage_record_create(self):
        record = UsageRecordCreate(
            tenant_id="t1",
            subscription_id="s1",
            collection="visitors",
            operation=OperationType.CREATE,
            endpoint="/v1/visitors",
            user_id="u1",
        )
        assert record.operation == OperationType.CREATE
        assert record.timestamp > 0

    def test_usage_aggregate_create(self):
        agg = UsageAggregateCreate(
            tenant_id="t1",
            subscription_id="s1",
            collection="visitors",
            operation=OperationType.CREATE,
            period_key="2026-04",
            count=42,
        )
        assert agg.count == 42
        assert agg.period_key == "2026-04"

    def test_tenant_usage_summary(self):
        summary = TenantUsageSummary(
            tenant_id="t1",
            plan_name="pro",
            plan_tier="professional",
            subscription_status="active",
            period="2026-04",
            crud_usage={"visitors": {"create": {"used": 50, "limit": 100}}},
            retrieval_usage={"dashboard": {"read": {"used": 10, "limit": 50}}},
            entity_counts={"system_users": 5},
            entity_caps={"max_system_users": 10},
            storage={"documents_used": 100, "documents_limit": 500},
        )
        assert summary.crud_usage["visitors"]["create"]["used"] == 50
        assert summary.entity_caps["max_system_users"] == 10
