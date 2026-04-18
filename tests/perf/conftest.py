"""Perf-suite fixtures.

Reuses the integration conftest for Mongo + Redis plumbing, then layers on
a realistic dataset so the measured endpoints actually have work to do —
matching the production pain points (tenant list, subscription list with
details, audit / visitor / incident scans, admin dashboards).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncGenerator

import pytest_asyncio
from bson import ObjectId
from httpx import AsyncClient

# Pull in every fixture defined in the integration conftest
# (mongo_db, redis_client, integration_app, integration_client, seeded_tenant,
#  seeded_system_user, auth_headers) without clashing with ruff's F811
# "redefined" check that triggers when we both import AND use the names.
pytest_plugins = ["tests.integration.conftest"]

from tests.integration.conftest import unique_password_suffix  # noqa: E402


_DEV_OTP = "123456"


# ---------------------------------------------------------------------------
# Application admin auth
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def perf_admin_headers(
    integration_client: AsyncClient,
    mongo_db,
) -> AsyncGenerator[dict[str, str], None]:
    """Create an application admin and return ``Authorization`` headers.

    Uses the real HTTP flow (signup via repo, then ``/v1/admins/login`` →
    ``/v1/admins/verify-otp``) so the token produced is identical to what
    a production client would hold.
    """
    from repositories.admin_repo import create_admin
    from schemas.admin_schema import AdminCreate

    suffix = unique_password_suffix()
    password = f"PerfAdmin_{suffix}!"
    email = f"perf_admin_{int(time.time())}_{uuid.uuid4().hex[:6]}@test.example.com"

    await create_admin(
        AdminCreate(
            full_name="Perf Admin",
            email=email,
            password=password,
            invited_by="perf",
        )
    )

    login_resp = await integration_client.post(
        "/v1/admins/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200, login_resp.text
    challenge_id = login_resp.json()["data"]["otp_challenge_id"]

    verify_resp = await integration_client.post(
        "/v1/admins/verify-otp",
        json={"otp_challenge_id": challenge_id, "otp_code": _DEV_OTP},
    )
    assert verify_resp.status_code == 200, verify_resp.text
    access_token = verify_resp.json()["data"]["access_token"]

    yield {"Authorization": f"Bearer {access_token}"}


# ---------------------------------------------------------------------------
# Realistic dataset
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def perf_dataset(
    mongo_db,
    seeded_tenant,
    auth_headers: dict[str, str],
    perf_admin_headers: dict[str, str],
) -> AsyncGenerator[dict[str, Any], None]:
    """Seed enough rows to exercise the endpoints that were previously slow.

    - 30 extra tenants so ``GET /v1/tenants`` actually enriches a page.
    - One active plan + one subscription per tenant so plan enforcement
      lets tenant-scoped reads through.
    - 20 visitors / 10 incidents / 5 appointments / audit events on the
      focus tenant so tenant-scoped list endpoints have something to page.

    Returns a dict of identifiers that the test suite uses to build URLs.
    """
    from services.plan_service import add_plan
    from services.subscription_service import subscribe_tenant
    from schemas.plan_schema import PlanCreate, PlanStatus, PlanTier
    from schemas.subscription_schema import BillingCycle
    from schemas.tenant_schema import TenantCreate
    from schemas.imports import LawfulBasis, NoticeDisplayMode
    from repositories.tenant_repo import create_tenant

    ts = int(time.time())

    # --- One active plan that every seeded tenant subscribes to ----------
    plan = await add_plan(
        PlanCreate(
            name=f"perf-plan-{ts}",
            display_name="Perf Plan",
            tier=PlanTier.PROFESSIONAL,
            status=PlanStatus.ACTIVE,
            base_price_monthly=100.0,
            base_price_yearly=1000.0,
            currency="NGN",
            is_public=True,
        )
    )
    plan_id = plan.id or ""

    # --- Focus tenant = the already-seeded_tenant, get a subscription ---
    focus_tenant_id = seeded_tenant.id or ""
    await subscribe_tenant(
        tenant_id=focus_tenant_id,
        plan_id=plan_id,
        billing_cycle=BillingCycle.MONTHLY,
    )

    # --- 30 other tenants, each with an active subscription -------------
    other_tenant_ids: list[str] = []
    for i in range(30):
        t = await create_tenant(
            TenantCreate(
                company_name=f"Perf Co {ts} #{i}",
                lawful_basis=LawfulBasis("legitimate_interest"),
                notice_display_mode=NoticeDisplayMode("passive"),
                retention_days=365,
            )
        )
        tid = t.id or ""
        other_tenant_ids.append(tid)
        await subscribe_tenant(
            tenant_id=tid,
            plan_id=plan_id,
            billing_cycle=BillingCycle.MONTHLY,
        )

    # --- Rows on the focus tenant for tenant-scoped list endpoints ------
    db = mongo_db
    now = ts

    visitors = [
        {
            "tenant_id": focus_tenant_id,
            "full_name": f"Perf Visitor {i}",
            "status": "checked_in" if i % 2 == 0 else "checked_out",
            "check_in_time": now - (i * 60),
            "host_user_id": None,
            "date_created": now,
            "last_updated": now,
        }
        for i in range(20)
    ]
    await db["visitors"].insert_many(visitors)

    incidents = [
        {
            "tenant_id": focus_tenant_id,
            "title": f"Perf Incident {i}",
            "status": "open" if i < 5 else "resolved",
            "severity": "low",
            "detection_time": now - (i * 3600),
            "reporter_id": "seed",
            "date_created": now,
            "last_updated": now,
        }
        for i in range(10)
    ]
    await db["incidents"].insert_many(incidents)

    appointments = [
        {
            "tenant_id": focus_tenant_id,
            "scheduled_time": now + (i * 3600),
            "visitor_name": f"Appt Visitor {i}",
            "status": "scheduled",
            "date_created": now,
            "last_updated": now,
        }
        for i in range(5)
    ]
    await db["appointments"].insert_many(appointments)

    audit_events: list[dict[str, Any]] = [
        {
            "tenant_id": focus_tenant_id,
            "actor_id": "seed",
            "actor_role": "super_admin",
            "action": f"seed.event.{i}",
            "resource_type": "seed",
            "resource_id": str(ObjectId()),
            "timestamp": now - (i * 60),
            "details": {},
        }
        for i in range(15)
    ]
    await db["audit_trail"].insert_many(audit_events)

    yield {
        "plan_id": plan_id,
        "focus_tenant_id": focus_tenant_id,
        "other_tenant_ids": other_tenant_ids,
        "admin_headers": perf_admin_headers,
        "super_admin_headers": auth_headers,
    }
