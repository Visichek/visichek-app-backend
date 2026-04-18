"""Endpoint speed smoke test.

Runs a realistic request set against the real stack (Mongo + Redis, via the
integration fixtures) and asserts server-side ``X-Process-Time`` stays under
a per-endpoint budget. The goal is catching regressions — for example, if
someone reverts the bulk-plan resolver, ``tenants_list_admin`` cold time
goes from ~50 ms to ~20 s and CI yells.

Two passes per endpoint:
  1. **Cold** — fresh request, exercises handler + DB.
  2. **Warm** — identical request, exercises HTTP cache hit.

Both timings are recorded. A consolidated ``perf_report.json`` is always
written for CI to upload as an artifact and for humans to eyeball.

The suite seeds a realistic dataset first (30 tenants + subs, 20 visitors,
10 incidents, 5 appointments, 15 audit events) so list endpoints have
something to page rather than returning empty.

Local run::

    pytest tests/perf -v

CI config lives in ``.github/workflows/ci.yml`` (``perf-smoke`` job).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Callable, List, Tuple

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio]


# Per-endpoint budgets in milliseconds (server-side, from ``X-Process-Time``).
# Budgets are deliberately loose — CI runners are noisy; we're catching
# order-of-magnitude regressions, not setting SLOs.
# Shape: endpoint_key -> (cold_ms, warm_ms)
_BUDGETS_MS: dict[str, Tuple[int, int]] = {
    # Baseline infrastructure
    "health_live": (50, 20),
    "health_full": (150, 30),
    # Public-ish reads
    "plans_list_public": (300, 80),
    # Super_admin tenant-scoped reads
    "user_settings_get": (400, 80),
    "notifications_unread_count": (400, 80),
    "settings_manifest": (800, 150),
    "visitors_list": (600, 100),
    "incidents_list": (600, 100),
    "appointments_list": (400, 80),
    "audit_logs_list": (800, 120),
    "branches_list": (400, 80),
    "tenant_settings_get": (400, 80),
    "tenant_active_subscription": (400, 100),
    "checkout_sessions_list": (400, 80),
    "tenant_dashboard_stats": (1200, 200),
    # Application-admin reads — the original pain points
    "tenants_list_admin": (800, 120),
    "subscriptions_list_admin": (1000, 200),
    "admin_dashboard_stats": (2500, 400),
    "admin_dashboard_billing": (2000, 400),
    "invoices_list_admin": (800, 150),
}


_REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "perf_report.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _time_request(
    client: AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
) -> Tuple[int, float]:
    """Return (status_code, server_ms). Falls back to wall-clock if the
    response lacks ``X-Process-Time`` (e.g. a short-circuit from the
    middleware stack before timing middleware runs)."""
    wall_start = time.perf_counter()
    resp = await client.request(method, url, headers=headers, json=json_body)
    wall_ms = (time.perf_counter() - wall_start) * 1000
    header_val = resp.headers.get("x-process-time")
    if header_val:
        try:
            return resp.status_code, float(header_val) * 1000
        except ValueError:
            pass
    return resp.status_code, wall_ms


def _write_report(entries: List[dict]) -> None:
    try:
        _REPORT_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError:
        pass


async def _run_case(
    client: AsyncClient,
    name: str,
    build: Callable[[], Tuple[str, str, dict, dict | None]],
) -> dict:
    method, url, headers, body = build()
    cold_status, cold_ms = await _time_request(
        client, method, url, headers=headers, json_body=body
    )
    warm_status, warm_ms = await _time_request(
        client, method, url, headers=headers, json_body=body
    )
    return {
        "name": name,
        "method": method,
        "url": url,
        "cold_status": cold_status,
        "warm_status": warm_status,
        "cold_ms": round(cold_ms, 2),
        "warm_ms": round(warm_ms, 2),
    }


def _build_cases(
    super_admin_headers: dict,
    admin_headers: dict,
    focus_tenant_id: str,
) -> list[Tuple[str, Callable[[], Tuple[str, str, dict, dict | None]]]]:
    """Build the list of cases. Lambdas are used so headers are captured
    at call-time rather than at module-import time (they come from fixtures)."""

    sa: dict[str, Any] = super_admin_headers
    adm: dict[str, Any] = admin_headers

    return [
        # --- infra -------------------------------------------------------
        ("health_live", lambda: ("GET", "/health/live", {}, None)),
        ("health_full", lambda: ("GET", "/health", {}, None)),
        # --- public/semi-public -----------------------------------------
        (
            "plans_list_public",
            lambda: ("GET", "/v1/plans?public_only=true", sa, None),
        ),
        # --- super_admin tenant-scoped ----------------------------------
        (
            "user_settings_get",
            lambda: ("GET", "/v1/user-settings", sa, None),
        ),
        (
            "notifications_unread_count",
            lambda: ("GET", "/v1/notifications/unread-count", sa, None),
        ),
        (
            "settings_manifest",
            lambda: ("GET", "/v1/settings", sa, None),
        ),
        (
            "visitors_list",
            lambda: ("GET", "/v1/visitors?limit=50", sa, None),
        ),
        (
            "incidents_list",
            lambda: ("GET", "/v1/incidents?limit=50", sa, None),
        ),
        (
            "appointments_list",
            lambda: ("GET", "/v1/appointments?limit=50", sa, None),
        ),
        (
            "audit_logs_list",
            lambda: ("GET", "/v1/audit-logs?limit=50", sa, None),
        ),
        (
            "branches_list",
            lambda: ("GET", "/v1/branches", sa, None),
        ),
        (
            "tenant_settings_get",
            lambda: ("GET", "/v1/tenant-settings", sa, None),
        ),
        (
            "tenant_active_subscription",
            lambda: (
                "GET",
                f"/v1/subscriptions/tenant/{focus_tenant_id}/active",
                sa,
                None,
            ),
        ),
        (
            "checkout_sessions_list",
            lambda: ("GET", "/v1/checkout/sessions", sa, None),
        ),
        (
            "tenant_dashboard_stats",
            lambda: ("GET", "/v1/dashboard/stats", sa, None),
        ),
        # --- application admin (the previous pain points) ---------------
        (
            "tenants_list_admin",
            lambda: ("GET", "/v1/tenants?stop=50", adm, None),
        ),
        (
            "subscriptions_list_admin",
            lambda: ("GET", "/v1/subscriptions?limit=50", adm, None),
        ),
        (
            "admin_dashboard_stats",
            lambda: ("GET", "/v1/admins/dashboard/stats", adm, None),
        ),
        (
            "admin_dashboard_billing",
            lambda: ("GET", "/v1/admins/dashboard/billing", adm, None),
        ),
        (
            "invoices_list_admin",
            lambda: ("GET", "/v1/invoices/admin?limit=50", adm, None),
        ),
    ]


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------


async def test_endpoint_speed_budget(
    integration_client: AsyncClient,
    perf_dataset: dict[str, Any],
) -> None:
    """One consolidated test — shared setup, per-case timings, one report.

    Using a single test keeps the fixture setup cost (seeding + admin login)
    amortised across every case. Each case runs cold+warm so the report
    shows where the HTTP cache is actually paying off.
    """
    super_admin_headers = perf_dataset["super_admin_headers"]
    admin_headers = perf_dataset["admin_headers"]
    focus_tenant_id = perf_dataset["focus_tenant_id"]

    cases = _build_cases(super_admin_headers, admin_headers, focus_tenant_id)

    report: List[dict] = []
    budget_failures: List[str] = []
    status_failures: List[str] = []

    for name, build in cases:
        entry = await _run_case(integration_client, name, build)
        report.append(entry)

        # Status check: any non-2xx is informational but recorded so the
        # log shows which endpoints couldn't serve with the seeded
        # dataset (often a 403 / 404 — we don't assert those hard).
        if not (200 <= entry["cold_status"] < 300):
            status_failures.append(
                f"{name}: cold_status={entry['cold_status']} warm_status={entry['warm_status']}"
            )
            continue  # skip budget check for non-2xx; they're not comparable

        cold_budget, warm_budget = _BUDGETS_MS.get(name, (5000, 1000))
        if entry["cold_ms"] > cold_budget:
            budget_failures.append(
                f"{name} cold {entry['cold_ms']}ms > {cold_budget}ms budget"
            )
        if entry["warm_ms"] > warm_budget:
            budget_failures.append(
                f"{name} warm {entry['warm_ms']}ms > {warm_budget}ms budget"
            )

    # --- Pretty report to stdout for the CI log -------------------------
    cold_values = [e["cold_ms"] for e in report if 200 <= e["cold_status"] < 300]
    warm_values = [e["warm_ms"] for e in report if 200 <= e["warm_status"] < 300]
    summary = (
        "\nPERF SUMMARY  "
        "cases={cases}  "
        "cold p50={cold_p50:.1f}ms p95={cold_p95:.1f}ms  "
        "warm p50={warm_p50:.1f}ms p95={warm_p95:.1f}ms".format(
            cases=len(report),
            cold_p50=statistics.median(cold_values) if cold_values else 0,
            cold_p95=_p95(cold_values) if cold_values else 0,
            warm_p50=statistics.median(warm_values) if warm_values else 0,
            warm_p95=_p95(warm_values) if warm_values else 0,
        )
    )
    print(summary)
    for e in report:
        print(
            "  {name:32s} cold={cold_ms:8.1f}ms ({cold_status})  "
            "warm={warm_ms:8.1f}ms ({warm_status})".format(**e)
        )

    _write_report(report)

    if status_failures:
        print("\nNon-2xx responses (informational):")
        for s in status_failures:
            print("  " + s)

    if budget_failures and os.getenv("PERF_ENFORCE", "1") != "0":
        pytest.fail("Perf budget exceeded:\n  - " + "\n  - ".join(budget_failures))


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[k]
