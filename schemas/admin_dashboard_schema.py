from __future__ import annotations

from schemas.imports import *


class PlanDistribution(BaseModel):
    """Breakdown of subscriptions per plan."""

    plan_id: str
    plan_name: str
    plan_tier: str
    subscriber_count: int


class TopTenantByIncidents(BaseModel):
    """Tenant ranked by incident count."""

    tenant_id: str
    company_name: str
    incident_count: int


class TopTenantByVisitors(BaseModel):
    """Tenant ranked by visitor count."""

    tenant_id: str
    company_name: str
    visitor_count: int


class SubscriptionStatusBreakdown(BaseModel):
    """Count of subscriptions by status."""

    active: int = 0
    trialing: int = 0
    past_due: int = 0
    cancelled: int = 0
    suspended: int = 0
    expired: int = 0


class AdminDashboardStats(BaseModel):
    """Platform-wide statistics for application admin dashboard."""

    # User & tenant counts
    total_tenants: int = 0
    active_tenants: int = 0
    total_tenant_users: int = 0
    total_application_users: int = 0

    # Subscription stats
    total_subscriptions: int = 0
    subscription_breakdown: SubscriptionStatusBreakdown = Field(
        default_factory=SubscriptionStatusBreakdown
    )

    # Plan stats
    total_plans: int = 0
    active_plans: int = 0
    archived_plans: int = 0
    draft_plans: int = 0
    plan_distribution: List[PlanDistribution] = Field(default_factory=list)

    # Incident stats
    total_incidents: int = 0
    open_incidents: int = 0
    top_tenants_by_incidents: List[TopTenantByIncidents] = Field(default_factory=list)

    # Visitor stats
    total_visitors_all_time: int = 0
    visitors_this_month: int = 0
    top_tenants_by_visitors: List[TopTenantByVisitors] = Field(default_factory=list)

    # Revenue (aggregate of effective prices)
    total_monthly_revenue: float = 0.0
    total_yearly_revenue: float = 0.0

    # Recent activity
    recent_signups_30d: int = 0

    last_updated: int = Field(default_factory=lambda: int(time.time()))
