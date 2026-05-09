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

    Every numeric field defaults to 0 / empty list, so a fresh tenant with
    no traffic still gets a fully-shaped response (frontend can render
    every chart in its empty state).

    Sections are deliberately flat (no nested ``data`` wrapper) so a chart
    can bind directly to ``stats.visit_status_distribution`` etc."""

    # ─── Overview KPIs ────────────────────────────────────────────────
    total_visits: int = 0
    total_visitors: int = 0  # unique visitor profiles (lifetime)
    total_appointments: int = 0
    total_departments: int = 0
    total_branches: int = 0
    total_system_users: int = 0
    total_incidents: int = 0
    open_incidents: int = 0
    critical_incidents: int = 0

    # ─── Live state ───────────────────────────────────────────────────
    currently_active: int = 0
    awaiting_checkout: int = 0
    pending_approval: int = 0  # checkins still waiting on receptionist
    pending_kyc: int = 0  # checkins in pending_verification

    # ─── Today snapshot ───────────────────────────────────────────────
    visitors_today: int = 0
    check_ins_today: int = 0
    check_outs_today: int = 0
    expected_today: int = 0  # appointments scheduled for today
    new_visitors_today: int = 0  # first-time visitors who showed today
    returning_visitors_today: int = 0  # visitors with prior visits
    denials_today: int = 0
    incidents_today: int = 0
    peak_hour_today: Optional[int] = None  # busiest hour 0..23 from today's check-ins

    # ─── Period totals ────────────────────────────────────────────────
    visits_this_week: int = 0
    visits_last_week: int = 0
    visits_this_month: int = 0
    visits_last_month: int = 0
    visits_this_quarter: int = 0
    visits_this_year: int = 0

    # ─── Growth ───────────────────────────────────────────────────────
    visits_growth_dod: GrowthMetric = Field(default_factory=GrowthMetric)
    visits_growth_wow: GrowthMetric = Field(default_factory=GrowthMetric)
    visits_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)
    signups_growth_wow: GrowthMetric = Field(default_factory=GrowthMetric)
    signups_growth_mom: GrowthMetric = Field(default_factory=GrowthMetric)

    # ─── Visit duration ───────────────────────────────────────────────
    avg_visit_duration_minutes: float = 0.0
    avg_visit_duration_seconds: int = 0  # raw seconds, frontend can format
    longest_visit_today_minutes: float = 0.0
    shortest_visit_today_minutes: float = 0.0
    overdue_checkouts: int = 0  # currently active visits past their expected_duration

    # ─── Visitor acquisition (marketing) ──────────────────────────────
    new_signups_today: int = 0
    new_signups_7d: int = 0
    new_signups_30d: int = 0
    new_signups_this_month: int = 0
    new_signups_last_month: int = 0
    returning_visitor_count: int = 0  # visitors with total_visits >= 2
    vip_visitor_count: int = 0  # visitors with total_visits >= 5
    visitor_retention_rate: float = 0.0  # % of profiles with > 1 visit

    # ─── Pie charts ───────────────────────────────────────────────────
    new_vs_returning: List[DistributionSlice] = Field(default_factory=list)
    visit_status_distribution: List[DistributionSlice] = Field(default_factory=list)
    check_in_method_distribution: List[DistributionSlice] = Field(default_factory=list)
    check_out_method_distribution: List[DistributionSlice] = Field(default_factory=list)
    verification_status_distribution: List[DistributionSlice] = Field(
        default_factory=list
    )
    verification_method_distribution: List[DistributionSlice] = Field(
        default_factory=list
    )
    consent_distribution: List[DistributionSlice] = Field(default_factory=list)
    badge_format_distribution: List[DistributionSlice] = Field(default_factory=list)
    purpose_distribution: List[DistributionSlice] = Field(default_factory=list)
    appointment_status_distribution: List[DistributionSlice] = Field(
        default_factory=list
    )
    incident_type_distribution: List[DistributionSlice] = Field(default_factory=list)
    incident_status_distribution: List[DistributionSlice] = Field(default_factory=list)
    kyc_status_distribution: List[DistributionSlice] = Field(default_factory=list)

    # ─── Top lists ────────────────────────────────────────────────────
    top_departments: List[TopItem] = Field(default_factory=list)
    top_hosts: List[TopItem] = Field(default_factory=list)
    top_companies: List[TopItem] = Field(default_factory=list)  # marketing gold
    top_visitors: List[TopItem] = Field(default_factory=list)  # frequent flyers
    top_branches: List[TopItem] = Field(default_factory=list)
    top_purposes: List[TopItem] = Field(default_factory=list)
    top_denial_reasons: List[TopItem] = Field(default_factory=list)
    top_check_in_methods: List[TopItem] = Field(default_factory=list)

    # ─── Time series (line / bar) ─────────────────────────────────────
    visits_last_7_days: List[TimeSeriesPoint] = Field(default_factory=list)
    visits_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    signups_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    appointments_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    incidents_last_30_days: List[TimeSeriesPoint] = Field(default_factory=list)
    check_outs_last_7_days: List[TimeSeriesPoint] = Field(default_factory=list)

    # ─── Heatmaps ─────────────────────────────────────────────────────
    hourly_distribution: List[HourlyBucket] = Field(default_factory=list)
    day_of_week_distribution: List[DayOfWeekBucket] = Field(default_factory=list)

    # ─── Appointment funnel ───────────────────────────────────────────
    appointments_scheduled: int = 0
    appointments_fulfilled: int = 0
    appointments_missed: int = 0
    appointments_cancelled: int = 0
    appointment_fulfillment_rate: float = 0.0
    appointment_no_show_rate: float = 0.0
    appointment_conversion_rate: float = 0.0  # appts → visits

    # ─── Operational quality ──────────────────────────────────────────
    verification_rate: float = 0.0  # verified / total
    consent_rate: float = 0.0  # consent_granted / total
    consent_withdrawal_count: int = 0
    denial_rate: float = 0.0
    badge_issue_rate: float = 0.0  # badge generated / total
    avg_kyc_pass_rate: float = 0.0  # success / (success+failed+expired)

    # ─── Real-time samples ────────────────────────────────────────────
    recent_check_ins: List[RecentCheckIn] = Field(default_factory=list)
    upcoming_appointments_today: List[UpcomingAppointment] = Field(default_factory=list)

    # ─── Compliance ───────────────────────────────────────────────────
    open_dsr_requests: int = 0
    total_dsr_requests: int = 0
    dsr_requests_30d: int = 0
    incidents_approaching_deadline: int = 0
    privacy_notices_count: int = 0
    retention_policies_count: int = 0
    sub_processors_count: int = 0

    # ─── Audit ────────────────────────────────────────────────────────
    total_audit_events: int = 0
    audit_events_today: int = 0
    audit_events_7d: int = 0

    # ─── Meta ─────────────────────────────────────────────────────────
    role_view: Optional[str] = None
    department_id: Optional[str] = None
    period: Dict[str, int] = Field(default_factory=dict)
    last_updated: int = Field(default_factory=lambda: int(time.time()))
