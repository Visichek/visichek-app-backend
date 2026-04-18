"""Endpoint speed smoke test.

Runs a small set of representative requests against the real stack (Mongo +
Redis, via the integration fixtures) and asserts that each endpoint's
server-side ``X-Process-Time`` stays under a per-endpoint budget. The budgets
are deliberately loose — the goal is catching regressions (e.g. accidentally
reverting an N+1 fix), not measuring latency to the microsecond.

Each case runs twice:
  1. **Cold** — first request; the HTTP cache middleware has no entry yet.
  2. **Warm** — second identical request; exercises the cache hit path.

Both timings are recorded so the final report shows where caching is
actually paying off. A summary is always written to ``perf_report.json`` at
the repo root so CI can upload it as an artifact.

Usage locally::

    pytest tests/perf -v

Usage in CI is configured in ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Callable, List

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio]

# Per-endpoint budgets in milliseconds (server-side, from ``X-Process-Time``).
# The "cold" budget includes cache-miss + handler + DB; "warm" is cache-hit.
# Leave headroom — CI runners are noisy; we only want to flag order-of-
# magnitude regressions.
_BUDGETS_MS = {
    # endpoint_key: (cold_ms, warm_ms)
    "health": (50, 20),
    "plans_list_public": (300, 50),
    "tenants_list_admin": (500, 50),
    "subscriptions_list_admin": (600, 80),
    "user_settings_get": (400, 50),
    "notifications_unread_count": (400, 50),
    "settings_manifest": (800, 80),
}


_REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "perf_report.json"


async def _time_request(
    client: AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
) -> tuple[int, float]:
    """Run one request; return (status_code, server_ms).

    Falls back to wall-clock if the response lacks ``X-Process-Time`` (e.g.
    when the middleware short-circuited on an early auth error).
    """
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
    """Persist a structured perf report so CI can upload / diff it."""
    try:
        _REPORT_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError:
        pass


async def _run_case(
    client: AsyncClient,
    name: str,
    do: Callable[[], "tuple[str, str, dict, dict | None]"],
    expected_status: tuple[int, ...] = (200,),
) -> dict:
    method, url, headers, body = do()
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
        "expected_status": expected_status,
    }


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------


async def test_endpoint_speed_budget(
    integration_client: AsyncClient,
    auth_headers: dict,
) -> None:
    """One big test with many sub-cases. Using one test keeps startup cost low
    (Mongo + Redis fixtures are shared across cases) and lets us write a
    single consolidated report at the end.
    """
    report: List[dict] = []
    failures: List[str] = []

    cases = [
        (
            "health",
            lambda: ("GET", "/health/live", {}, None),
        ),
        (
            "plans_list_public",
            lambda: (
                "GET",
                "/v1/plans?public_only=true",
                auth_headers,
                None,
            ),
        ),
        (
            "user_settings_get",
            lambda: ("GET", "/v1/user-settings", auth_headers, None),
        ),
        (
            "notifications_unread_count",
            lambda: (
                "GET",
                "/v1/notifications/unread-count",
                auth_headers,
                None,
            ),
        ),
        (
            "settings_manifest",
            lambda: ("GET", "/v1/settings", auth_headers, None),
        ),
    ]

    for name, do in cases:
        entry = await _run_case(integration_client, name, do)
        report.append(entry)
        cold_budget, warm_budget = _BUDGETS_MS.get(name, (2000, 500))
        if entry["cold_ms"] > cold_budget:
            failures.append(
                f"{name} cold {entry['cold_ms']}ms > {cold_budget}ms budget"
            )
        if entry["warm_ms"] > warm_budget:
            failures.append(
                f"{name} warm {entry['warm_ms']}ms > {warm_budget}ms budget"
            )

    # Summary line at the end makes the CI log grep-friendly.
    cold_values = [e["cold_ms"] for e in report]
    warm_values = [e["warm_ms"] for e in report]
    print(
        "\nPERF SUMMARY  cold p50={:.1f}ms p95={:.1f}ms  warm p50={:.1f}ms p95={:.1f}ms".format(
            statistics.median(cold_values) if cold_values else 0,
            max(cold_values) if cold_values else 0,
            statistics.median(warm_values) if warm_values else 0,
            max(warm_values) if warm_values else 0,
        )
    )
    for e in report:
        print(
            "  {name:30s} cold={cold_ms:7.1f}ms warm={warm_ms:7.1f}ms "
            "cold_status={cold_status} warm_status={warm_status}".format(**e)
        )

    _write_report(report)

    if failures and os.getenv("PERF_ENFORCE", "1") != "0":
        pytest.fail("Perf budget exceeded:\n  - " + "\n  - ".join(failures))
