from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

# Reuse the chart building blocks already shipped on the tenant dashboard so
# the same frontend chart components render both the legacy /dashboard/stats
# payload and the new range-aware /dashboard/insights payload.
from schemas.dashboard_stats_schema import (  # noqa: F401 — re-exported for FE parity
    DistributionSlice,
    HourlyBucket,
    RecentCheckIn,
    TimeSeriesPoint,
    TopItem,
)


class Trend(BaseModel):
    """Window-over-window comparison for a KPI.

    ``change_percent`` compares the selected window against the immediately
    preceding window of the SAME length. ``is_good`` lets the UI colour the
    arrow correctly — "up" is not always good (e.g. denials up = bad)."""

    change_percent: float = 0.0
    direction: str = "flat"  # "up" | "down" | "flat"
    is_good: bool = True


class Kpi(BaseModel):
    """One KPI card. ``trend`` is ``None`` for live/current metrics that have
    no meaningful preceding-window comparison, and always ``None`` on Free."""

    key: str
    label: str
    value: Union[int, float, str]
    unit: Optional[str] = None
    trend: Optional[Trend] = None
    description: str = ""


class InsightsSection(BaseModel):
    """A single chart block. ``type`` discriminates which payload field is
    populated; the others stay ``None``:

    - ``timeSeries``   -> ``points`` (+ ``value_label``)
    - ``distribution`` -> ``slices``
    - ``hourly``       -> ``buckets``
    - ``topList``      -> ``items``
    - ``feed``         -> ``events``

    ``meta`` carries section-specific extras (e.g. ``pastDeadline`` count on
    the incident distribution for the security-officer red banner)."""

    type: str
    title: str
    points: Optional[List[TimeSeriesPoint]] = None
    value_label: Optional[str] = None
    slices: Optional[List[DistributionSlice]] = None
    buckets: Optional[List[HourlyBucket]] = None
    items: Optional[List[TopItem]] = None
    events: Optional[List[RecentCheckIn]] = None
    # ``table`` type (admin insights only): flat camelCase rows + the ordered
    # list of keys to render. The tenant side never needs this.
    rows: Optional[List[Dict[str, Any]]] = None
    columns: Optional[List[str]] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class InsightsMeta(BaseModel):
    """Range / role / plan envelope returned alongside every Insights payload."""

    role_view: str
    department_id: Optional[str] = None
    branch_id: Optional[str] = None
    tenant_created_at: int = 0  # range-picker lower bound
    earliest_data: int = 0  # first day we actually have data (>= created_at)
    applied_range: Dict[str, int] = Field(
        default_factory=dict
    )  # post-clamp {start,stop}
    granularity: str = "day"  # "hour" | "day" | "week" | "month"
    plan_tier: str = "free"
    available_sections: List[str] = Field(default_factory=list)
    locked_sections: List[str] = Field(default_factory=list)
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class InsightsResponse(BaseModel):
    """Top-level payload for ``GET /v1/dashboard/insights``."""

    meta: InsightsMeta
    kpis: List[Kpi] = Field(default_factory=list)
    sections: Dict[str, InsightsSection] = Field(default_factory=dict)


class AdminInsightsMeta(BaseModel):
    """Range envelope for the PLATFORM-ADMIN insights endpoint.

    No plan/role gating here — an application admin sees everything. The lower
    bound is the platform (first tenant), not a single tenant's creation date."""

    platform_launch_at: int = 0  # earliest selectable date (first tenant)
    earliest_data: int = 0  # first day with platform data (>= launch)
    applied_range: Dict[str, int] = Field(default_factory=dict)  # post-clamp {start,stop}
    granularity: str = "day"  # "hour" | "day" | "week" | "month"
    tab: str = "overview"
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class AdminInsightsResponse(BaseModel):
    """Top-level payload for ``GET /v1/admins/dashboard/insights``."""

    meta: AdminInsightsMeta
    kpis: List[Kpi] = Field(default_factory=list)
    sections: Dict[str, InsightsSection] = Field(default_factory=dict)
