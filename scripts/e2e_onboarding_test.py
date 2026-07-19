"""End-to-end tenant-onboarding test against a DEPLOYED backend.

Drives the full lifecycle a real tenant goes through, using the
non-production test-email bypass (core/test_mode.py):

  1.  Health probes.
  2.  Public marketing-form submission (POST /v1/onboarding/submissions)
      with a ``@visichek.test`` work email — Turnstile is skipped for
      test emails outside production.
  3.  Application-admin login (+ static dev OTP) and verification that
      the submission landed in the database.
  4.  Admin accepts the submission → tenant + super_admin provisioned
      with the fixed TEST_TEMP_PASSWORD (no email leaves the system).
  5.  Super_admin first login → must_change_password gate verified →
      password changed → re-login with the new password.
  6.  First-login self-onboarding endpoints (pending fields, tenant
      confirmation, DPA).
  7.  Invites two more users (receptionist + dept_admin), logs in as
      the receptionist.
  8.  Authority password reset on the receptionist → re-login with the
      fixed temp password → change it.
  9.  Platform usage: create a department + branch (queued writes,
      polled via /v1/jobs/{job_id}), dashboards, settings, sessions,
      notifications.
  10. OpenAPI-driven GET sweep: every parameterless GET endpoint is
      called as admin AND as super_admin; 5xx responses fail the run.
  11. Optional cleanup: --offboard tears the tenant back down.

Usage:
    python scripts/e2e_onboarding_test.py \
        --base-url https://api.visichek.app \
        --admin-email you@example.com --admin-password '...'

    Environment fallbacks: VISICHEK_API_BASE, VISICHEK_ADMIN_EMAIL,
    VISICHEK_ADMIN_PASSWORD, VISICHEK_OTP_CODE (default 123456),
    VISICHEK_TEST_DOMAIN (default visichek.test),
    VISICHEK_TEST_TEMP_PASSWORD (default VisiChekT3st!Pass).

The script is intentionally dependency-light: httpx + stdlib only.
Exit code 0 = every step passed; 1 = at least one failure (summary
table printed either way).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Result bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    steps: list[StepResult] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append(StepResult(name, ok, detail))
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok]


REPORT = Report()


# ---------------------------------------------------------------------------
# HTTP client with envelope handling, camelCase tolerance, 429 retry
# ---------------------------------------------------------------------------


def snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.title() for part in rest)


def pick(data: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict tolerating camelCase/snake_case responses."""
    if not isinstance(data, dict):
        return default
    if key in data:
        return data[key]
    camel = snake_to_camel(key)
    if camel in data:
        return data[camel]
    return default


class Api:
    """One authenticated identity against the backend."""

    def __init__(self, base_url: str, label: str) -> None:
        self.label = label
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=30.0,
            follow_redirects=True,
            headers={"X-Auth-Include-Tokens": "1"},
        )
        self.token: Optional[str] = None

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        max_attempts: int = 5,
    ) -> httpx.Response:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        attempt = 0
        while True:
            attempt += 1
            response = self.client.request(
                method, path, json=json_body, params=params, headers=headers
            )
            if response.status_code != 429 or attempt >= max_attempts:
                return response
            wait = float(response.headers.get("Retry-After", "0") or 0) or min(
                60.0, 5.0 * attempt
            )
            print(f"      ... 429 on {method} {path}; backing off {wait:.0f}s")
            time.sleep(wait)

    def envelope(self, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except Exception:
            return None
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def adopt_tokens(self, data: Any) -> bool:
        """Capture access token from a login/verify/change-password payload.

        Handles both flat payloads and nested ``{user, access_token}``
        shapes; the httpx cookie jar picks up the httpOnly cookies too.
        """
        if not isinstance(data, dict):
            return False
        token = pick(data, "access_token")
        if not token:
            tokens = pick(data, "tokens")
            if isinstance(tokens, dict):
                token = pick(tokens, "access_token")
        if not token and isinstance(pick(data, "user"), dict):
            token = pick(pick(data, "user"), "access_token")
        if token:
            self.token = token
            return True
        # Cookie-only response is still a success — the jar has them.
        return bool(self.client.cookies.get("access_token"))

    # -- higher-level helpers -------------------------------------------

    def poll_job(self, job_id: str, timeout_s: float = 60.0) -> dict:
        """Poll GET /v1/jobs/{job_id} until succeeded/failed."""
        deadline = time.time() + timeout_s
        last: dict = {}
        while time.time() < deadline:
            response = self.request("GET", f"/v1/jobs/{job_id}")
            data = self.envelope(response) or {}
            status = pick(data, "status", "")
            last = data if isinstance(data, dict) else {}
            if status in {"succeeded", "failed"}:
                return last
            time.sleep(1.5)
        return last


def login_with_otp(
    api: Api,
    login_path: str,
    verify_path: str,
    email: str,
    password: str,
    otp_code: str,
) -> tuple[bool, dict, str]:
    """Login handling all three response shapes (complete / OTP /
    tenant-selection). Returns (ok, final_payload, detail)."""
    response = api.request(
        "POST", login_path, json_body={"email": email, "password": password}
    )
    if response.status_code != 200:
        return False, {}, f"{login_path} -> HTTP {response.status_code}: {response.text[:300]}"
    data = api.envelope(response) or {}

    if pick(data, "tenant_selection_required"):
        selection_token = pick(data, "selection_token")
        tenants = pick(data, "tenants") or []
        tenant_id = pick(tenants[0], "tenant_id") if tenants else None
        response = api.request(
            "POST",
            "/v1/system-users/select-tenant",
            json_body={"selection_token": selection_token, "tenant_id": tenant_id},
        )
        if response.status_code != 200:
            return False, {}, f"select-tenant -> HTTP {response.status_code}"
        data = api.envelope(response) or {}

    if pick(data, "otp_required"):
        challenge_id = pick(data, "otp_challenge_id")
        response = api.request(
            "POST",
            verify_path,
            json_body={"otp_challenge_id": challenge_id, "otp_code": otp_code},
        )
        if response.status_code != 200:
            return False, {}, f"{verify_path} -> HTTP {response.status_code}: {response.text[:300]}"
        data = api.envelope(response) or {}

    if not api.adopt_tokens(data):
        return False, data, "login succeeded but no access token in body or cookies"
    return True, data, ""


# ---------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------


FIELD_LABELS = {
    "full_name": "Full name",
    "work_email": "Work email",
    "phone_number": "Phone number",
    "role": "Your role",
    "organization_name": "Organization name",
    "country": "Country",
    "organization_type": "Organization type",
    "current_method": "Current visitor process",
    "visitors_per_month": "Visitors per month",
    "departments": "Departments",
    "priorities": "Priorities",
    "access_control": "Access control",
    "timeline": "Timeline",
    "marketing_opt_in": "Marketing opt-in",
    "agreed_to_policies": "Agreed to policies",
}
FIELD_ORDER = list(FIELD_LABELS.keys())


def build_submission(owner_email: str, org_name: str, owner_name: str) -> dict:
    payload = {
        "full_name": owner_name,
        "work_email": owner_email,
        "phone_number": "+2348012345678",
        "role": "Operations Lead",
        "organization_name": org_name,
        "country": "Nigeria",
        "organization_type": "Corporate office",
        "current_method": "Paper logbook",
        "visitors_per_month": "101-500",
        "departments": ["Front Desk", "Security"],
        "priorities": ["Visitor tracking", "Compliance"],
        "access_control": "None",
        "timeline": "This month",
        "marketing_opt_in": "no",
        "agreed_to_policies": True,
    }
    return {
        "form_version": "2026-05-01",
        "payload": payload,
        "field_labels": FIELD_LABELS,
        "field_order": FIELD_ORDER,
        "turnstile_token": "e2e-test-bypass",
    }


def run(args: argparse.Namespace) -> int:
    run_id = uuid.uuid4().hex[:8]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    owner_email = f"owner.{stamp}.{run_id}@{args.test_domain}"
    org_name = f"E2E Test Org {stamp}-{run_id}"
    owner_name = "E2E Test Owner"
    temp_password = args.test_temp_password
    owner_password = f"Owner!Chg9.{run_id}Aa"
    invite_password = f"Invite!Chg7.{run_id}Bb"

    print(f"== VisiChek onboarding E2E ==\n   base={args.base_url}\n   owner={owner_email}\n   org={org_name}\n")

    public = Api(args.base_url, "public")
    admin = Api(args.base_url, "admin")
    owner = Api(args.base_url, "owner")
    invitee = Api(args.base_url, "invitee")

    tenant_id: Optional[str] = None
    submission_id: Optional[str] = None
    receptionist_id: Optional[str] = None

    try:
        # -- 1. health ---------------------------------------------------
        for probe in ("/health/live", "/health/ready"):
            response = public.request("GET", probe)
            REPORT.record(
                f"health {probe}",
                response.status_code == 200,
                f"HTTP {response.status_code}",
            )

        # -- 2. public form submission ----------------------------------
        response = public.request(
            "POST",
            "/v1/onboarding/submissions",
            json_body=build_submission(owner_email, org_name, owner_name),
        )
        data = public.envelope(response) or {}
        submission_id = pick(data, "id")
        if not REPORT.record(
            "public onboarding submission",
            response.status_code in (200, 201) and bool(submission_id),
            f"HTTP {response.status_code} id={submission_id} {response.text[:200] if response.status_code >= 400 else ''}",
        ):
            return finish()

        # -- 3. admin login + submission visible in DB -------------------
        ok, _, detail = login_with_otp(
            admin,
            "/v1/admins/login",
            "/v1/admins/verify-otp",
            args.admin_email,
            args.admin_password,
            args.otp_code,
        )
        if not REPORT.record("application-admin login", ok, detail):
            return finish()

        response = admin.request("GET", f"/v1/tenants/onboarding/{submission_id}")
        row = admin.envelope(response) or {}
        REPORT.record(
            "submission persisted (admin read-back)",
            response.status_code == 200
            and pick(row, "email") == owner_email.lower()
            and pick(row, "status") in ("new", "NEW"),
            f"HTTP {response.status_code} status={pick(row, 'status')} email={pick(row, 'email')}",
        )

        # -- 4. accept → tenant + super_admin ---------------------------
        response = admin.request(
            "POST", f"/v1/tenants/onboarding/{submission_id}/accept", json_body={}
        )
        data = admin.envelope(response) or {}
        tenant_id = pick(data, "tenant_id")
        if not REPORT.record(
            "admin accepts submission (tenant provisioned)",
            response.status_code in (200, 201) and bool(tenant_id),
            f"HTTP {response.status_code} tenant_id={tenant_id} {response.text[:200] if response.status_code >= 400 else ''}",
        ):
            return finish()

        # -- 5. super_admin first login / password gate ------------------
        ok, payload, detail = login_with_otp(
            owner,
            "/v1/system-users/login",
            "/v1/system-users/verify-otp",
            owner_email,
            temp_password,
            args.otp_code,
        )
        if not REPORT.record(
            "super_admin first login with fixed temp password", ok, detail
        ):
            return finish()

        user_obj = pick(payload, "user") if isinstance(pick(payload, "user"), dict) else payload
        REPORT.record(
            "must_change_password=true on first login",
            bool(pick(user_obj, "must_change_password")),
            json.dumps({k: pick(user_obj, k) for k in ("must_change_password",)}),
        )

        response = owner.request("GET", "/v1/departments")
        REPORT.record(
            "password gate blocks normal endpoints (403 expected)",
            response.status_code == 403,
            f"GET /v1/departments -> HTTP {response.status_code}",
        )

        response = owner.request(
            "POST",
            "/v1/auth/change-password",
            json_body={
                "current_password": temp_password,
                "new_password": owner_password,
            },
        )
        change_data = owner.envelope(response)
        owner.adopt_tokens(change_data or {})
        REPORT.record(
            "super_admin changes password",
            response.status_code == 200,
            f"HTTP {response.status_code} {response.text[:200] if response.status_code >= 400 else ''}",
        )

        ok, payload, detail = login_with_otp(
            owner,
            "/v1/system-users/login",
            "/v1/system-users/verify-otp",
            owner_email,
            owner_password,
            args.otp_code,
        )
        REPORT.record("super_admin re-login with NEW password", ok, detail)

        response = owner.request("GET", "/v1/system-users/me")
        me = owner.envelope(response) or {}
        REPORT.record(
            "GET /v1/system-users/me after password change",
            response.status_code == 200
            and not pick(me, "must_change_password", False),
            f"HTTP {response.status_code} must_change_password={pick(me, 'must_change_password')}",
        )

        # -- 6. first-login self-onboarding endpoints --------------------
        for method, path, body in (
            ("GET", "/v1/onboarding/me/pending-fields", None),
            ("GET", "/v1/onboarding/me/tenant-confirmation", None),
            ("POST", "/v1/onboarding/me/tenant-confirmation", {}),
            ("GET", "/v1/onboarding/me/dpa", None),
        ):
            response = owner.request(method, path, json_body=body)
            REPORT.record(
                f"onboarding self-service {method} {path}",
                response.status_code in (200, 201, 202),
                f"HTTP {response.status_code} {response.text[:150] if response.status_code >= 400 else ''}",
            )

        # -- 7. invite additional users ---------------------------------
        invites = [
            ("receptionist", f"reception.{stamp}.{run_id}@{args.test_domain}"),
            ("dept_admin", f"deptadmin.{stamp}.{run_id}@{args.test_domain}"),
        ]
        invite_ids: dict[str, str] = {}
        for role, email in invites:
            response = owner.request(
                "POST",
                "/v1/system-users/signup",
                json_body={
                    "full_name": f"E2E {role.title()}",
                    "email": email,
                    "password": invite_password,
                    "role": role,
                },
            )
            data = owner.envelope(response) or {}
            if response.status_code == 202 and pick(data, "job_id"):
                job = owner.poll_job(pick(data, "job_id"))
                created_id = pick(job, "resource_id") or pick(
                    pick(job, "result") or {}, "id"
                )
                ok = pick(job, "status") == "succeeded"
            else:
                created_id = pick(data, "id") or pick(
                    pick(data, "user") or {}, "id"
                )
                ok = response.status_code in (200, 201)
            if created_id:
                invite_ids[role] = str(created_id)
            REPORT.record(
                f"invite {role}",
                ok,
                f"HTTP {response.status_code} id={created_id} {response.text[:150] if response.status_code >= 400 else ''}",
            )
        receptionist_id = invite_ids.get("receptionist")

        ok, _, detail = login_with_otp(
            invitee,
            "/v1/system-users/login",
            "/v1/system-users/verify-otp",
            invites[0][1],
            invite_password,
            args.otp_code,
        )
        REPORT.record("invited receptionist login", ok, detail)

        # -- 8. authority password reset on the receptionist -------------
        if receptionist_id:
            response = owner.request(
                "POST", f"/v1/system-users/{receptionist_id}/reset-password"
            )
            reset_accepted = response.status_code in (200, 202)
            if response.status_code == 202:
                data = owner.envelope(response) or {}
                job_id = pick(data, "job_id")
                if job_id:
                    reset_accepted = pick(owner.poll_job(job_id), "status") == "succeeded"
            REPORT.record(
                "authority password reset (receptionist)",
                reset_accepted,
                f"HTTP {response.status_code}",
            )
            if reset_accepted:
                ok, _, detail = login_with_otp(
                    invitee,
                    "/v1/system-users/login",
                    "/v1/system-users/verify-otp",
                    invites[0][1],
                    temp_password,
                    args.otp_code,
                )
                REPORT.record(
                    "receptionist re-login with fixed temp password after reset",
                    ok,
                    detail,
                )
                response = invitee.request(
                    "POST",
                    "/v1/auth/change-password",
                    json_body={
                        "current_password": temp_password,
                        "new_password": f"Recep!Chg5.{run_id}Cc",
                    },
                )
                invitee.adopt_tokens(invitee.envelope(response) or {})
                REPORT.record(
                    "receptionist changes password after reset",
                    response.status_code == 200,
                    f"HTTP {response.status_code}",
                )
        else:
            REPORT.record(
                "authority password reset (receptionist)",
                False,
                "no receptionist id captured from invite step",
            )

        # -- 9. platform usage as super_admin ---------------------------
        response = owner.request(
            "POST", "/v1/departments", json_body={"name": f"E2E Dept {run_id}"}
        )
        data = owner.envelope(response) or {}
        dept_ok = response.status_code in (200, 201)
        if response.status_code == 202 and pick(data, "job_id"):
            dept_ok = pick(owner.poll_job(pick(data, "job_id")), "status") == "succeeded"
        REPORT.record(
            "create department (queued write + job poll)",
            dept_ok,
            f"HTTP {response.status_code} {response.text[:150] if response.status_code >= 400 else ''}",
        )

        response = owner.request(
            "POST", "/v1/branches", json_body={"name": f"E2E Branch {run_id}"}
        )
        data = owner.envelope(response) or {}
        branch_ok = response.status_code in (200, 201)
        if response.status_code == 202 and pick(data, "job_id"):
            branch_ok = (
                pick(owner.poll_job(pick(data, "job_id")), "status") == "succeeded"
            )
        REPORT.record(
            "create branch (queued write + job poll)",
            branch_ok,
            f"HTTP {response.status_code} {response.text[:150] if response.status_code >= 400 else ''}",
        )

        for path in (
            "/v1/departments",
            "/v1/branches",
            "/v1/dashboard/stats",
            "/v1/settings",
            "/v1/sessions",
            "/v1/user-settings",
            "/v1/tenant-settings",
            "/v1/notifications",
            "/v1/notifications/unread-count",
            "/v1/visitors",
            "/v1/appointments",
        ):
            response = owner.request("GET", path)
            REPORT.record(
                f"super_admin GET {path}",
                response.status_code == 200,
                f"HTTP {response.status_code}",
            )
            time.sleep(args.pace)

        # -- 10. OpenAPI-driven GET sweep --------------------------------
        if not args.skip_sweep:
            sweep(public, admin, owner, args)

        # -- 11. optional cleanup ---------------------------------------
        if args.offboard and tenant_id:
            response = admin.request(
                "POST",
                f"/v1/admins/tenants/{tenant_id}/offboard",
                json_body={"reason": f"E2E cleanup {run_id}"},
            )
            REPORT.record(
                "offboard tenant (cleanup)",
                response.status_code in (200, 202),
                f"HTTP {response.status_code}",
            )

        return finish()
    finally:
        for api in (public, admin, owner, invitee):
            api.close()


def sweep(public: Api, admin: Api, owner: Api, args: argparse.Namespace) -> None:
    """Call every parameterless GET endpoint as admin and as super_admin.

    Expectation model: anything except 5xx is a *handled* response
    (401/403 = role gate working, 402 = plan gate, 404 = empty state,
    400 = missing query params). 5xx fails the run.
    """
    response = public.request("GET", "/openapi.json")
    if response.status_code != 200:
        REPORT.record("openapi sweep", False, f"/openapi.json -> {response.status_code}")
        return
    spec = response.json()
    paths = spec.get("paths", {})
    get_paths = sorted(
        path
        for path, ops in paths.items()
        if "get" in ops and "{" not in path
    )
    print(f"\n-- OpenAPI GET sweep: {len(get_paths)} parameterless GET endpoints --")
    failures: list[str] = []
    checked = 0
    for path in get_paths:
        for api in (admin, owner):
            response = api.request("GET", path, max_attempts=8)
            checked += 1
            if response.status_code >= 500:
                failures.append(f"{api.label} GET {path} -> {response.status_code}")
                print(f"   [5xx] {api.label:5s} GET {path} -> {response.status_code}")
            time.sleep(args.pace)
    REPORT.record(
        f"openapi GET sweep ({checked} calls across {len(get_paths)} endpoints)",
        not failures,
        "; ".join(failures[:10]) if failures else "no 5xx responses",
    )


def finish() -> int:
    print("\n== SUMMARY ==")
    passed = sum(1 for s in REPORT.steps if s.ok)
    print(f"{passed}/{len(REPORT.steps)} steps passed")
    for step in REPORT.failed:
        print(f"  FAIL: {step.name} — {step.detail}")
    return 0 if not REPORT.failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("VISICHEK_API_BASE", "http://localhost:8000"),
    )
    parser.add_argument(
        "--admin-email", default=os.getenv("VISICHEK_ADMIN_EMAIL", "")
    )
    parser.add_argument(
        "--admin-password", default=os.getenv("VISICHEK_ADMIN_PASSWORD", "")
    )
    parser.add_argument(
        "--otp-code", default=os.getenv("VISICHEK_OTP_CODE", "123456")
    )
    parser.add_argument(
        "--test-domain", default=os.getenv("VISICHEK_TEST_DOMAIN", "visichek.test")
    )
    parser.add_argument(
        "--test-temp-password",
        default=os.getenv("VISICHEK_TEST_TEMP_PASSWORD", "VisiChekT3st!Pass"),
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=float(os.getenv("VISICHEK_PACE_SECONDS", "0.25")),
        help="Delay between sweep calls to stay under per-role rate limits.",
    )
    parser.add_argument("--skip-sweep", action="store_true")
    parser.add_argument(
        "--offboard",
        action="store_true",
        help="Offboard the created tenant at the end (cleanup).",
    )
    args = parser.parse_args()

    if not args.admin_email or not args.admin_password:
        print(
            "ERROR: application-admin credentials required "
            "(--admin-email/--admin-password or VISICHEK_ADMIN_EMAIL/"
            "VISICHEK_ADMIN_PASSWORD)."
        )
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
