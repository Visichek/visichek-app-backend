from __future__ import annotations

import time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# Re-use the shared chart primitives from the tenant dashboard so frontend
# components can render either dashboard with the same renderers.
from schemas.dashboard_stats_schema import (  # noqa: F401 — re-exported
    DayOfWeekBucket,
    DistributionSlice,
    GrowthMetric,
    HourlyBucket,
    TimeSeriesPoint,
)


class PlanDistribution(BaseModel):
    """Subscribers per plan, with revenue contribution."""

    plan_id: str
    plan_name: str
    plan_tier: str
    subscriber_count: int
    monthly_revenue: float = 0.0
    yearly_revenue: float = 0.0
    percentage: float = 0.0


class TopTenantByIncidents(BaseModel):
    tenant_id: str
    company_name: str
    incident_count: int


class TopTenantByVisitors(BaseModel):
    tenant_id: str
    company_name: str
    visitor_count: int


class TopTenantByRevenue(BaseModel):
    tenant_id: str
    company_name: str
    monthly_revenue: float
    yearly_revenue: float
    plan_name: Optional[str] = None
    plan_tier: Optional[str] = None
    status: Optional[str] = None


class TopTenantByActivity(BaseModel):
    """Tenant ranked by visit-session volume (active operational use)."""

    tenant_id: str
    company_name: str
    check_ins_30d: int
    total_visit_sessions: int


class TopTenantBySupport(BaseModel):
    tenant_id: str
    company_name: str
    open_cases: int
    total_cases: int


class SubscriptionStatusBreakdown(BaseModel):
    """Count of subscriptions by status. Mirrors SubscriptionStatus enum."""

    active: int = 0
    trialing: int = 0
    past_due: int = 0
    cancelled: int = 0
    suspended: int = 0
    expired: int = 0


class InvoiceStatusBreakdown(BaseModel):
    draft: int = 0
    issued: int = 0
    paid: int = 0
    void: int = 0
    refunded: int = 0


class SystemUserRoleBreakdown(BaseModel):
    """Count of tenant-side users by role."""

    super_admin: int = 0
    dept_admin: int = 0
    receptionist: int = 0
    auditor: int = 0
    security_officer: int = 0
    dpo: int = 0


class TenantBriefRow(BaseModel):
    """Compact tenant row used in 'recent signups' / 'recently active' lists."""

    id: str
    company_name: str
    is_active: bool
    country_of_hosting: Optional[str] = None
    date_created: Optional[int] = None
    plan_name: Optional[str] = None
    plan_tier: Optional[str] = None
    subscription_status: Optional[str] = None
    visitors_count: Optional[int] = None


class AdminDashboardStats(BaseModel):
    """Comprehensive platform-wide statistics for application admins.

    Returned by ``GET /v1/admins/dashboard/stats``. Every field defaults
    to a zero / empty value so a freshly-deployed platform with no
    tenants still produces a fully-shaped response."""

    # ─── Tenant overview ──────────────────────────────────────────────
    total_tenants: int = 0
    active_tenants: int = 0
    inactive_tenants: int = 0
    new_tenants_today: int = 0
    new_tenants_7d: int = 0
    new_tenants_30d: int = 0
    new_tenants_this_month: int = 0
    new_tenants_last_month: int = 0
    tenants_growth_wow: GrowthMetric = Field(default_factory=GrowthMetric)
    tenants_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)

    # ─── User overview ────────────────────────────────────────────────
    total_tenant_users: int = 0
    total_application_users: int = 0
    total_application_admins: int = 0
    total_visitors_all_time: int = 0
    visitors_today: int = 0
    visitors_7d: int = 0
    visitors_this_month: int = 0
    visitors_last_month: int = 0
    visitors_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)
    system_user_role_breakdown: SystemUserRoleBreakdown = Field(
        default_factory=SystemUserRoleBreakdown
    )

    # ─── Subscriptions ────────────────────────────────────────────────
    total_subscriptions: int = 0
    active_subscriptions: int = 0
    trialing_subscriptions: int = 0
    past_due_subscriptions: int = 0
    suspended_subscriptions: int = 0
    cancelled_30d: int = 0
    new_subscriptions_30d: int = 0
    subscriptions_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)
    churn_rate_30d: float = 0.0  # cancelled_30d / active_at_period_start × 100
    trial_conversion_rate: float = 0.0
    subscription_breakdown: SubscriptionStatusBreakdown = Field(
        default_factory=SubscriptionStatusBreakdown
    )

    # ─── Plan analytics ───────────────────────────────────────────────
    total_plans: int = 0
    active_plans: int = 0
    archived_plans: int = 0
    draft_plans: int = 0
    plan_distribution: List[PlanDistribution] = Field(default_factory=list)
    plan_tier_distribution: List[DistributionSlice] = Field(default_factory=list)
    billing_cycle_distribution: List[DistributionSlice] = Field(default_factory=list)
    payment_provider_distribution: List[DistributionSlice] = Field(default_factory=list)

    # ─── Revenue & billing ────────────────────────────────────────────
    total_monthly_revenue: float = 0.0  # MRR (sum of monthly subs' effective_price)
    total_yearly_revenue: float = 0.0  # Sum of yearly subs' effective_price
    mrr: float = 0.0  # monthly_revenue + (yearly_revenue / 12)
    arr: float = 0.0  # mrr * 12
    revenue_30d_minor: int = 0  # paid invoices last 30d
    revenue_7d_minor: int = 0
    revenue_today_minor: int = 0
    avg_invoice_value_minor: int = 0
    invoice_count_30d: int = 0
    paid_invoice_count_30d: int = 0
    failed_invoice_count_30d: int = 0
    invoice_status_breakdown: InvoiceStatusBreakdown = Field(
        default_factory=InvoiceStatusBreakdown
    )
    revenue_growth_wow: GrowthMetric = Field(default_factory=GrowthMetric)
    revenue_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)

    # ─── Dunning / payments ───────────────────────────────────────────
    payments_succeeded_30d: int = 0
    payments_failed_30d: int = 0
    payment_success_rate: float = 0.0
    dunning_queue_size: int = 0  # PAST_DUE subscriptions awaiting retry

    # ─── Incidents (cross-tenant) ─────────────────────────────────────
    total_incidents: int = 0
    open_incidents: int = 0
    critical_incidents: int = 0
    incidents_today: int = 0
    incidents_30d: int = 0
    incident_type_distribution: List[DistributionSlice] = Field(default_factory=list)
    incident_status_distribution: List[DistributionSlice] = Field(default_factory=list)
    top_tenants_by_incidents: List[TopTenantByIncidents] = Field(default_factory=list)

    # ─── Visitors (cross-tenant) ──────────────────────────────────────
    visitor_check_ins_today: int = 0
    visitor_check_ins_7d: int = 0
    visitor_check_ins_30d: int = 0
    top_tenants_by_visitors: List[TopTenantByVisitors] = Field(default_factory=list)
    top_tenants_by_activity: List[TopTenantByActivity] = Field(default_factory=list)
    top_tenants_by_revenue: List[TopTenantByRevenue] = Field(default_factory=list)

    # ─── Geography (marketing) ────────────────────────────────────────
    tenants_by_country: List[DistributionSlice] = Field(default_factory=list)

    # ─── Onboarding pipeline (marketing-site leads) ───────────────────
    onboarding_total: int = 0
    onboarding_new: int = 0
    onboarding_accepted_30d: int = 0
    onboarding_rejected_30d: int = 0
    onboarding_completed_30d: int = 0
    onboarding_status_distribution: List[DistributionSlice] = Field(
        default_factory=list
    )
    onboarding_acceptance_rate: float = 0.0  # accepted / (accepted+rejected)

    # ─── Support cases ────────────────────────────────────────────────
    support_cases_total: int = 0
    support_cases_open: int = 0
    support_cases_30d: int = 0
    support_status_distribution: List[DistributionSlice] = Field(default_factory=list)
    support_priority_distribution: List[DistributionSlice] = Field(default_factory=list)
    support_category_distribution: List[DistributionSlice] = Field(default_factory=list)
    top_tenants_by_support: List[TopTenantBySupport] = Field(default_factory=list)

    # ─── Compliance / DSR (cross-tenant) ──────────────────────────────
    dsr_open: int = 0
    dsr_total: int = 0
    dsr_30d: int = 0
    dsr_status_distribution: List[DistributionSlice] = Field(default_factory=list)
    incidents_approaching_deadline: int = 0

    # ─── Time series (last 30 days) ───────────────────────────────────
    tenant_signups_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    subscription_signups_last_30_days: List[TimeSeriesPoint] = Field(
        default_factory=list
    )
    visitor_signups_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    visit_check_ins_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    revenue_last_30_days_minor: List[TimeSeriesPoint] = Field(default_factory=list)
    incidents_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)

    # ─── Heatmaps (cross-tenant check-in activity, last 30 days) ──────
    hourly_distribution: List[HourlyBucket] = Field(default_factory=list)
    day_of_week_distribution: List[DayOfWeekBucket] = Field(default_factory=list)

    # ─── Recent activity ──────────────────────────────────────────────
    recent_tenant_signups: List[TenantBriefRow] = Field(default_factory=list)
    recently_active_tenants: List[TenantBriefRow] = Field(default_factory=list)

    # ─── Meta ─────────────────────────────────────────────────────────
    period: Dict[str, int] = Field(default_factory=dict)
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    # ─── Legacy aliases (kept to avoid breaking existing UIs) ─────────
    recent_signups_30d: int = 0


# ─── Attention queue (Issue 1 backend) ────────────────────────────────


class AttentionItem(BaseModel):
    """A single actionable item rendered on the admin dashboard.

    Mirrors the frontend's ``AttentionItem`` type one-for-one so the
    dashboard panel can swap from its derived-from-stats fallback to
    this authoritative endpoint with a single hook change.

    Field semantics:
      - ``priority``: one of ``blocker | urgent | normal | informational``
        — drives the badge color in the UI and the top-of-list sort.
      - ``owner_area``: one of ``support | content | billing |
        onboarding | system | security`` — drives a small group label
        so users can scan by function.
      - ``count``: optional. When present the UI renders a pill with
        this number; absent for "review this" cues that don't have a
        countable backing item set.
      - ``href``: relative path the user lands on when the card is
        clicked. Always a registered route — the frontend's
        route-existence test catches drift.
      - ``due_at`` / ``snoozed_until`` / ``dismissed_at``: reserved for
        future dismissible items. Always ``None`` from this endpoint
        today.
    """

    id: str
    priority: str  # "blocker" | "urgent" | "normal" | "informational"
    title: str
    reason: str
    count: Optional[int] = None
    href: str
    owner_area: str
    due_at: Optional[int] = None
    snoozed_until: Optional[int] = None
    dismissed_at: Optional[int] = None


class AttentionQueue(BaseModel):
    """Response envelope for ``GET /v1/admins/dashboard/attention``.

    Carries the rendered item list plus a small summary header so the
    dashboard can show "N urgent" without re-counting client-side.
    """

    items: List[AttentionItem] = Field(default_factory=list)
    blocker_count: int = 0
    urgent_count: int = 0
    generated_at: int = Field(default_factory=lambda: int(time.time()))
