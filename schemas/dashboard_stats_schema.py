from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DistributionSlice(BaseModel):
    """One slice of a pie / donut chart.

    ``label`` is the display string (already humanised — e.g. ``"Checked in"``
    not ``"checked_in"``). ``value`` is the absolute count. ``percentage``
    is rounded to one decimal and sums to ~100 across the parent list."""

    key: str
    label: str
    value: int
    percentage: float = 0.0


class TimeSeriesPoint(BaseModel):
    """One bucket on a daily-granularity time series."""

    timestamp: int  # unix ts at the start of the bucket (UTC midnight)
    label: str  # ISO date "YYYY-MM-DD"
    value: int


class HourlyBucket(BaseModel):
    """One hour of the 24-hour heatmap."""

    hour: int  # 0..23
    label: str  # "00:00" .. "23:00"
    value: int


class DayOfWeekBucket(BaseModel):
    """One day of the Mon..Sun heatmap."""

    day: int  # 0=Mon ... 6=Sun (matches Python ``datetime.weekday()``)
    label: str  # "Mon".."Sun"
    value: int


class TopItem(BaseModel):
    """A ranked entry in a "top X" list. ``id`` is null for free-form
    groupings (e.g. company name strings) that don't have a stable id."""

    id: Optional[str] = None
    label: str
    value: int
    percentage: float = 0.0
    extra: Dict[str, Any] = Field(default_factory=dict)


class GrowthMetric(BaseModel):
    """A current-vs-previous-period comparison."""

    current: int = 0
    previous: int = 0
    change: int = 0  # current - previous
    change_percent: float = 0.0  # rounded to 1 decimal; 0 when previous=0


class RecentCheckIn(BaseModel):
    """Compact preview row for the "recent check-ins" widget."""

    id: str
    visitor_name: Optional[str] = None
    company: Optional[str] = None
    department_name: Optional[str] = None
    host_name: Optional[str] = None
    purpose: Optional[str] = None
    status: Optional[str] = None
    check_in_time: Optional[int] = None
    check_out_time: Optional[int] = None
    duration_minutes: Optional[int] = None
    verified: bool = False


class UpcomingAppointment(BaseModel):
    id: str
    visitor_name: Optional[str] = None
    host_name: Optional[str] = None
    department_id: Optional[str] = None
    purpose: Optional[str] = None
    status: Optional[str] = None
    scheduled_datetime: Optional[int] = None


class TenantDashboardStats(BaseModel):
    """Comprehensive tenant dashboard payload.

    The "basic" fields (counters, live state, today snapshot) always have a
    numeric / list default so they're populated for every tier. Every other
    field defaults to ``None`` and is only populated for paid tiers — on
    Free, those fields stay ``null`` and the frontend renders an upgrade
    nudge in their place. See ``upgrade_required_for`` for the capability
    keys the UI uses to drive those nudges.

    Sections are deliberately flat (no nested ``data`` wrapper) so a chart
    can bind directly to ``stats.visit_status_distribution`` etc."""

    # ─── Plan / upgrade signaling ─────────────────────────────────────
    plan_tier: str = "free"
    upgrade_required_for: List[str] = Field(default_factory=list)

    # ─── Overview KPIs (basic — always populated) ─────────────────────
    total_visits: int = 0
    total_visitors: int = 0  # unique visitor profiles (lifetime)
    total_departments: int = 0
    total_system_users: int = 0
    # ─── Overview KPIs (paid — null on Free) ──────────────────────────
    total_appointments: Optional[int] = None
    total_branches: Optional[int] = None
    total_incidents: Optional[int] = None
    open_incidents: Optional[int] = None
    critical_incidents: Optional[int] = None

    # ─── Live state (basic — always populated) ────────────────────────
    currently_active: int = 0
    awaiting_checkout: int = 0
    pending_approval: int = 0  # checkins still waiting on receptionist
    # ─── Live state (paid — null on Free) ─────────────────────────────
    pending_kyc: Optional[int] = None  # checkins in pending_verification

    # ─── Today snapshot (basic — always populated) ────────────────────
    visitors_today: int = 0
    check_ins_today: int = 0
    check_outs_today: int = 0
    new_visitors_today: int = 0  # first-time visitors who showed today
    returning_visitors_today: int = 0  # visitors with prior visits
    denials_today: int = 0
    # ─── Today snapshot (paid — null on Free) ─────────────────────────
    expected_today: Optional[int] = None  # appointments scheduled for today
    incidents_today: Optional[int] = None
    peak_hour_today: Optional[int] = None  # busiest hour 0..23 from today's check-ins

    # ─── Period totals (paid — null on Free) ──────────────────────────
    visits_this_week: Optional[int] = None
    visits_last_week: Optional[int] = None
    visits_this_month: Optional[int] = None
    visits_last_month: Optional[int] = None
    visits_this_quarter: Optional[int] = None
    visits_this_year: Optional[int] = None

    # ─── Growth (paid — null on Free) ─────────────────────────────────
    visits_growth_dod: Optional[GrowthMetric] = None
    visits_growth_wow: Optional[GrowthMetric] = None
    visits_growth_mom: Optional[GrowthMetric] = None
    signups_growth_wow: Optional[GrowthMetric] = None
    signups_growth_mom: Optional[GrowthMetric] = None

    # ─── Visit duration (paid — null on Free) ─────────────────────────
    avg_visit_duration_minutes: Optional[float] = None
    avg_visit_duration_seconds: Optional[int] = None  # raw seconds, frontend can format
    longest_visit_today_minutes: Optional[float] = None
    shortest_visit_today_minutes: Optional[float] = None
    overdue_checkouts: Optional[int] = (
        None  # currently active visits past expected_duration
    )

    # ─── Visitor acquisition / marketing (paid — null on Free) ────────
    new_signups_today: Optional[int] = None
    new_signups_7d: Optional[int] = None
    new_signups_30d: Optional[int] = None
    new_signups_this_month: Optional[int] = None
    new_signups_last_month: Optional[int] = None
    returning_visitor_count: Optional[int] = None  # visitors with total_visits >= 2
    vip_visitor_count: Optional[int] = None  # visitors with total_visits >= 5
    visitor_retention_rate: Optional[float] = None  # % of profiles with > 1 visit

    # ─── Pie charts (paid — null on Free) ─────────────────────────────
    new_vs_returning: Optional[List[DistributionSlice]] = None
    visit_status_distribution: Optional[List[DistributionSlice]] = None
    check_in_method_distribution: Optional[List[DistributionSlice]] = None
    check_out_method_distribution: Optional[List[DistributionSlice]] = None
    verification_status_distribution: Optional[List[DistributionSlice]] = None
    verification_method_distribution: Optional[List[DistributionSlice]] = None
    consent_distribution: Optional[List[DistributionSlice]] = None
    badge_format_distribution: Optional[List[DistributionSlice]] = None
    purpose_distribution: Optional[List[DistributionSlice]] = None
    appointment_status_distribution: Optional[List[DistributionSlice]] = None
    incident_type_distribution: Optional[List[DistributionSlice]] = None
    incident_status_distribution: Optional[List[DistributionSlice]] = None
    kyc_status_distribution: Optional[List[DistributionSlice]] = None

    # ─── Top lists (paid — null on Free) ──────────────────────────────
    top_departments: Optional[List[TopItem]] = None
    top_hosts: Optional[List[TopItem]] = None
    top_companies: Optional[List[TopItem]] = None  # marketing gold
    top_visitors: Optional[List[TopItem]] = None  # frequent flyers
    top_branches: Optional[List[TopItem]] = None
    top_purposes: Optional[List[TopItem]] = None
    top_denial_reasons: Optional[List[TopItem]] = None
    top_check_in_methods: Optional[List[TopItem]] = None

    # ─── Time series (paid — null on Free) ────────────────────────────
    visits_last_7_days: Optional[List[TimeSeriesPoint]] = None
    visits_last_30_days: Optional[List[TimeSeriesPoint]] = None
    signups_last_30_days: Optional[List[TimeSeriesPoint]] = None
    appointments_last_30_days: Optional[List[TimeSeriesPoint]] = None
    incidents_last_30_days: Optional[List[TimeSeriesPoint]] = None
    check_outs_last_7_days: Optional[List[TimeSeriesPoint]] = None

    # ─── Heatmaps (paid — null on Free) ───────────────────────────────
    hourly_distribution: Optional[List[HourlyBucket]] = None
    day_of_week_distribution: Optional[List[DayOfWeekBucket]] = None

    # ─── Appointment funnel (paid — null on Free) ─────────────────────
    appointments_scheduled: Optional[int] = None
    appointments_fulfilled: Optional[int] = None
    appointments_missed: Optional[int] = None
    appointments_cancelled: Optional[int] = None
    appointment_fulfillment_rate: Optional[float] = None
    appointment_no_show_rate: Optional[float] = None
    appointment_conversion_rate: Optional[float] = None  # appts → visits

    # ─── Operational quality (paid — null on Free) ────────────────────
    verification_rate: Optional[float] = None  # verified / total
    consent_rate: Optional[float] = None  # consent_granted / total
    consent_withdrawal_count: Optional[int] = None
    denial_rate: Optional[float] = None
    badge_issue_rate: Optional[float] = None  # badge generated / total
    avg_kyc_pass_rate: Optional[float] = None  # success / (success+failed+expired)

    # ─── Real-time samples (paid — null on Free) ──────────────────────
    recent_check_ins: Optional[List[RecentCheckIn]] = None
    upcoming_appointments_today: Optional[List[UpcomingAppointment]] = None

    # ─── Compliance (paid — null on Free) ─────────────────────────────
    open_dsr_requests: Optional[int] = None
    total_dsr_requests: Optional[int] = None
    dsr_requests_30d: Optional[int] = None
    incidents_approaching_deadline: Optional[int] = None
    privacy_notices_count: Optional[int] = None
    retention_policies_count: Optional[int] = None
    sub_processors_count: Optional[int] = None

    # ─── Audit (paid — null on Free) ──────────────────────────────────
    total_audit_events: Optional[int] = None
    audit_events_today: Optional[int] = None
    audit_events_7d: Optional[int] = None

    # ─── Meta ─────────────────────────────────────────────────────────
    role_view: Optional[str] = None
    department_id: Optional[str] = None
    period: Dict[str, int] = Field(default_factory=dict)
    last_updated: int = Field(default_factory=lambda: int(time.time()))
