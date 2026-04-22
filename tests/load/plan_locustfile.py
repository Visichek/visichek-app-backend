"""
Load tests for the subscription/plan system.
Tests plan CRUD, subscription lifecycle, and discount operations under load.

Run with:
    locust -f tests/load/plan_locustfile.py --host http://localhost:8000
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from locust import HttpUser, between, task

logger = logging.getLogger(__name__)

DEV_OTP_CODE = os.getenv("LOAD_TEST_OTP_CODE", "123456")
INCLUDE_TOKENS_HEADERS = {"X-Auth-Include-Tokens": "true"}


def _pick(data: dict, *keys: str) -> Optional[Any]:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _otp_challenge_id(data: dict) -> Optional[str]:
    if not (data.get("otp_required") or data.get("otpRequired")):
        return None
    return _pick(data, "otp_challenge_id", "otpChallengeId")


def admin_login_with_otp(client, email: str, password: str) -> Optional[str]:
    """Log in as application admin, handle 2FA, return the access token.

    Returns ``None`` on any failure. Reads ``accessToken`` / ``access_token``
    transparently to tolerate the CaseConversionMiddleware.
    """
    resp = client.post(
        "/v1/admins/login",
        json={"email": email, "password": password},
        headers=INCLUDE_TOKENS_HEADERS,
    )
    if resp.status_code != 200:
        logger.warning(f"Admin login failed: {resp.status_code} - {resp.text[:200]}")
        return None

    data = resp.json().get("data", {}) or {}
    challenge_id = _otp_challenge_id(data)
    if challenge_id:
        otp_resp = client.post(
            "/v1/admins/verify-otp",
            json={"otp_challenge_id": challenge_id, "otp_code": DEV_OTP_CODE},
            headers=INCLUDE_TOKENS_HEADERS,
        )
        if otp_resp.status_code != 200:
            logger.warning(
                f"Admin OTP verify failed: {otp_resp.status_code} - {otp_resp.text[:200]}"
            )
            return None
        data = otp_resp.json().get("data", {}) or {}

    return _pick(data, "access_token", "accessToken")


class PlanAdminUser(HttpUser):
    """Simulates a application admin managing plans, subscriptions, and discounts."""

    wait_time = between(0.5, 2)
    weight = 3

    admin_token: str | None = None
    created_plan_ids: list[str] = []
    created_discount_ids: list[str] = []

    def on_start(self):
        """Login as admin (handling 2FA)."""
        email = os.getenv("LOAD_TEST_ADMIN_EMAIL", "superadmin@visicheck.com")
        password = os.getenv("LOAD_TEST_ADMIN_PASSWORD", "@ViViVheck123!")
        self.admin_token = admin_login_with_otp(self.client, email, password)
        self.created_plan_ids = []
        self.created_discount_ids = []

    @property
    def auth_headers(self) -> dict:
        if self.admin_token:
            return {"Authorization": f"Bearer {self.admin_token}"}
        return {}

    @task(5)
    def create_plan(self):
        ts = int(time.time() * 1000)
        resp = self.client.post(
            "/v1/plans",
            json={
                "name": f"load-plan-{ts}",
                "display_name": f"Load Plan {ts}",
                "tier": "professional",
                "status": "active",
                "base_price_monthly": 99.99,
                "feature_rules": [
                    {"endpoint_pattern": "/v1/visitors/*", "enabled": True},
                ],
                "crud_limits": [
                    {
                        "collection": "visitors",
                        "max_create": 100,
                        "reset_interval": "monthly",
                    },
                ],
            },
            headers=self.auth_headers,
            name="/v1/plans [POST]",
        )
        if resp.status_code == 201:
            plan_id = resp.json()["data"]["id"]
            self.created_plan_ids.append(plan_id)

    @task(10)
    def list_plans(self):
        self.client.get(
            "/v1/plans",
            name="/v1/plans [GET list]",
        )

    @task(8)
    def get_plan(self):
        if self.created_plan_ids:
            plan_id = self.created_plan_ids[-1]
            self.client.get(
                f"/v1/plans/{plan_id}",
                headers=self.auth_headers,
                name="/v1/plans/{id} [GET]",
            )

    @task(3)
    def update_plan(self):
        if self.created_plan_ids:
            plan_id = self.created_plan_ids[-1]
            self.client.put(
                f"/v1/plans/{plan_id}",
                json={"base_price_monthly": 149.99},
                headers=self.auth_headers,
                name="/v1/plans/{id} [PUT]",
            )

    @task(3)
    def create_discount(self):
        ts = int(time.time() * 1000)
        resp = self.client.post(
            "/v1/discounts",
            json={
                "code": f"LOAD{ts}",
                "name": f"Load Discount {ts}",
                "discount_type": "percentage",
                "value": 15.0,
                "scope": "global",
            },
            headers=self.auth_headers,
            name="/v1/discounts [POST]",
        )
        if resp.status_code == 201:
            self.created_discount_ids.append(resp.json()["data"]["id"])

    @task(6)
    def list_discounts(self):
        self.client.get(
            "/v1/discounts",
            headers=self.auth_headers,
            name="/v1/discounts [GET list]",
        )


class SubscriptionUser(HttpUser):
    """Simulates subscription management operations."""

    wait_time = between(1, 3)
    weight = 2

    admin_token: str | None = None

    def on_start(self):
        email = os.getenv("LOAD_TEST_ADMIN_EMAIL", "superadmin@visicheck.com")
        password = os.getenv("LOAD_TEST_ADMIN_PASSWORD", "@ViViVheck123!")
        self.admin_token = admin_login_with_otp(self.client, email, password)

    @property
    def auth_headers(self) -> dict:
        if self.admin_token:
            return {"Authorization": f"Bearer {self.admin_token}"}
        return {}

    @task(8)
    def list_subscriptions(self):
        self.client.get(
            "/v1/subscriptions",
            headers=self.auth_headers,
            name="/v1/subscriptions [GET list]",
        )

    @task(3)
    def full_subscription_lifecycle(self):
        """Create plan -> bootstrap tenant -> subscribe -> cancel."""
        ts = int(time.time() * 1000)

        # Create plan
        plan_resp = self.client.post(
            "/v1/plans",
            json={
                "name": f"lifecycle-plan-{ts}",
                "display_name": f"Lifecycle {ts}",
                "status": "active",
                "base_price_monthly": 50.0,
            },
            headers=self.auth_headers,
            name="/v1/plans [POST lifecycle]",
        )
        if plan_resp.status_code != 201:
            return
        plan_id = plan_resp.json()["data"]["id"]

        # Bootstrap tenant
        tenant_resp = self.client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Load Corp {ts}",
                "admin_full_name": "Load SA",
                "admin_email": f"load_sa_{ts}@test.test",
                "admin_password": "LoadTest123!",
            },
            headers=self.auth_headers,
            name="/v1/admins/tenants/bootstrap [POST lifecycle]",
        )
        if tenant_resp.status_code != 201:
            return
        tenant_id = tenant_resp.json()["data"]["tenant"]["id"]

        # Subscribe
        sub_resp = self.client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=self.auth_headers,
            name="/v1/subscriptions [POST lifecycle]",
        )
        if sub_resp.status_code != 201:
            return

        # Cancel
        self.client.post(
            "/v1/subscriptions/cancel",
            json={"tenant_id": tenant_id, "immediate": True},
            headers=self.auth_headers,
            name="/v1/subscriptions/cancel [POST lifecycle]",
        )


class PlanCatalogUser(HttpUser):
    """Simulates unauthenticated users browsing the plan catalog."""

    wait_time = between(0.5, 1.5)
    weight = 5

    @task
    def browse_plans(self):
        self.client.get(
            "/v1/plans?public_only=true",
            name="/v1/plans [GET public catalog]",
        )
