from __future__ import annotations

import asyncio
import os
import sys
import random
import time
from typing import Optional

import httpx

BASE_URL = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")
# Dev backends hardcode the OTP to "123456" so all 2FA flows are scriptable.
# Override via env var if the target env uses a different fixed code.
DEV_OTP_CODE = os.getenv("LOAD_TEST_OTP_CODE", "123456")
INCLUDE_TOKENS = {"X-Auth-Include-Tokens": "true"}


def _pick(data: dict, *keys: str) -> Optional[str]:
    """Return the first present value for any of the given keys.

    The CaseConversionMiddleware emits camelCase by default (``accessToken``,
    ``tenantId``, ``otpChallengeId``), so every response reader needs to accept
    both forms. Pass snake_case first, camelCase second, by convention.
    """
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _otp_challenge_id(data: dict) -> Optional[str]:
    if not (data.get("otp_required") or data.get("otpRequired")):
        return None
    return _pick(data, "otp_challenge_id", "otpChallengeId")


async def _complete_otp_challenge(
    client: httpx.AsyncClient,
    *,
    verify_url: str,
    challenge_id: str,
) -> dict:
    """POST /verify-otp with the dev OTP and return the JSON data block."""
    resp = await client.post(
        verify_url,
        json={"otp_challenge_id": challenge_id, "otp_code": DEV_OTP_CODE},
        headers=INCLUDE_TOKENS,
    )
    if resp.status_code != 200:
        raise Exception(
            f"OTP verification failed: {resp.status_code} - {resp.text}"
        )
    return resp.json().get("data", {}) or {}


async def login_admin(
    client: httpx.AsyncClient, email: str, password: str
) -> dict:
    """Log in as application admin, handling the 2FA challenge if required."""
    resp = await client.post(
        f"{BASE_URL}/v1/admins/login",
        json={"email": email, "password": password},
        headers=INCLUDE_TOKENS,
    )
    if resp.status_code != 200:
        raise Exception(f"Admin login failed: {resp.status_code} - {resp.text}")

    data = resp.json().get("data", {}) or {}

    # Step 2 of 2FA: if the server issued an OTP challenge, complete it
    # with the fixed dev code. The verify-otp response shape matches the
    # no-2FA login response (AdminOut + tokens).
    challenge_id = _otp_challenge_id(data)
    if challenge_id:
        data = await _complete_otp_challenge(
            client,
            verify_url=f"{BASE_URL}/v1/admins/verify-otp",
            challenge_id=challenge_id,
        )

    token = _pick(data, "access_token", "accessToken")
    if not token:
        raise Exception(
            "Admin login returned no access_token — check X-Auth-Include-Tokens support"
        )
    return {
        "access_token": token,
        "admin_id": data.get("id"),
        "headers": {"Authorization": f"Bearer {token}"},
    }


async def login_system_user(
    client: httpx.AsyncClient, email: str, password: str
) -> Optional[dict]:
    """Log in as system user (super_admin, receptionist, etc.), handling 2FA."""
    resp = await client.post(
        f"{BASE_URL}/v1/system-users/login",
        json={"email": email, "password": password},
        headers=INCLUDE_TOKENS,
    )
    if resp.status_code != 200:
        return None

    data = resp.json().get("data", {}) or {}

    challenge_id = _otp_challenge_id(data)
    if challenge_id:
        try:
            data = await _complete_otp_challenge(
                client,
                verify_url=f"{BASE_URL}/v1/system-users/verify-otp",
                challenge_id=challenge_id,
            )
        except Exception:
            return None

    token = _pick(data, "access_token", "accessToken")
    if not token:
        return None
    return {
        "access_token": token,
        "user_id": data.get("id"),
        "tenant_id": _pick(data, "tenant_id", "tenantId"),
        "headers": {"Authorization": f"Bearer {token}"},
    }


async def bootstrap_tenant(
    client: httpx.AsyncClient,
    admin_headers: dict,
    *,
    company_name: str,
    admin_full_name: str,
    admin_email: str,
    admin_password: str,
) -> dict:
    """Bootstrap tenant + first super_admin atomically.

    Returns a dict with ``tenant_id``, ``super_admin_id``, and the super_admin's
    ``access_token`` / ``refresh_token`` — the bootstrap response embeds them
    directly, so no second login is required on the happy path.
    """
    payload = {
        "company_name": company_name,
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "passive",
        "retention_days": 30,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@loadtest.local",
        "privacy_policy_url": "https://example.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": True,
        "admin_full_name": admin_full_name,
        "admin_email": admin_email,
        "admin_password": admin_password,
    }

    resp = await client.post(
        f"{BASE_URL}/v1/admins/tenants/bootstrap",
        json=payload,
        headers=admin_headers,
    )
    if resp.status_code not in (200, 201):
        raise Exception(
            f"Bootstrap failed: {resp.status_code} - {resp.text}"
        )

    data = resp.json().get("data", {}) or {}
    tenant = data.get("tenant") or {}
    super_admin = data.get("super_admin") or data.get("superAdmin") or {}
    return {
        "tenant_id": tenant.get("id"),
        "super_admin_id": super_admin.get("id"),
        "access_token": _pick(super_admin, "access_token", "accessToken"),
        "refresh_token": _pick(super_admin, "refresh_token", "refreshToken"),
    }


async def create_system_user(
    client: httpx.AsyncClient,
    super_admin_headers: dict,
    email: str,
    full_name: str,
    role: str,
    department_id: Optional[str] = None,
) -> dict:
    """Invite a system user via super_admin token. `tenant_id` is inferred from the token."""
    payload: dict = {
        "full_name": full_name,
        "email": email,
        "password": "LoadTest@123",
        "role": role,
    }
    if department_id:
        payload["department_id"] = department_id

    response = await client.post(
        f"{BASE_URL}/v1/system-users/signup",
        json=payload,
        headers=super_admin_headers,
    )

    if response.status_code in (200, 201):
        user = response.json().get("data", {})
        print(f"✓ Created {role} user: {email} ({user.get('id')})")
        return user

    print(
        f"  Note: User {email} not created ({response.status_code}) — may already exist"
    )
    return {}


async def create_departments(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    count: int = 5,
) -> list[str]:
    """Create test departments via the write pipeline. Returns pre-assigned IDs."""
    dept_names = [
        "Reception",
        "Sales",
        "Engineering",
        "Marketing",
        "Human Resources",
        "Finance",
        "Operations",
        "Customer Support",
        "Legal",
        "IT",
    ]

    department_ids: list[str] = []
    for i in range(min(count, len(dept_names))):
        dept_data = {
            "tenant_id": tenant_id,
            "code": f"DEPT-{i + 1:03d}",
            "name": dept_names[i],
            "is_active": True,
        }

        response = await client.post(
            f"{BASE_URL}/v1/departments",
            json=dept_data,
            headers=headers,
        )

        # POST /v1/departments returns 202 + { id, job_id, status } via the
        # write pipeline. The id is pre-assigned, so we can stash it immediately
        # even though persistence happens asynchronously on worker-writes.
        if response.status_code in (201, 202):
            dept_id = response.json().get("data", {}).get("id")
            department_ids.append(dept_id)
            print(f"✓ Created department: {dept_names[i]} ({dept_id})")
        else:
            print(
                f"  Failed to create department {dept_names[i]}: {response.status_code} - {response.text[:200]}"
            )

    return department_ids


async def create_visitor_profile(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
) -> Optional[str]:
    """Create a visitor profile."""
    first_names = [
        "John",
        "Jane",
        "Michael",
        "Sarah",
        "David",
        "Emma",
        "Robert",
        "Lisa",
        "James",
        "Mary",
    ]
    last_names = [
        "Smith",
        "Johnson",
        "Williams",
        "Brown",
        "Jones",
        "Garcia",
        "Miller",
        "Davis",
        "Rodriguez",
        "Martinez",
    ]
    companies = [
        "Acme Corp",
        "Tech Solutions",
        "Global Industries",
        "Innovation Labs",
        "Future Systems",
        "Digital Dynamics",
        "Cloud Nexus",
        "Data Insights",
        "Smart Systems",
        "NextGen Corp",
    ]

    visitor_data = {
        "tenant_id": tenant_id,
        "full_name": f"{random.choice(first_names)} {random.choice(last_names)}",
        "phone": f"+234{random.randint(8000000000, 8099999999)}",
        "email_address": f"visitor{random.randint(10000, 99999)}@example.com",
        "company": random.choice(companies),
        "profiling_preference": "allowed",
    }

    response = await client.post(
        f"{BASE_URL}/v1/visitors",
        json=visitor_data,
        headers=headers,
    )

    if response.status_code in (200, 201, 202):
        return response.json().get("data", {}).get("id")
    return None


async def create_visitor_profiles(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    count: int = 50,
) -> list[str]:
    """Create multiple visitor profiles in parallel."""
    tasks = [create_visitor_profile(client, headers, tenant_id) for _ in range(count)]

    visitor_ids: list[str] = []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, str):
            visitor_ids.append(result)
        if (i + 1) % 10 == 0:
            print(f"✓ Created {i + 1}/{count} visitor profiles")

    print(f"✓ Total visitor profiles created: {len(visitor_ids)}")
    return visitor_ids


async def create_appointments(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    department_id: str,
    host_id: str,
    count: int = 10,
) -> list[str]:
    """Create sample appointments."""
    appointment_ids: list[str] = []
    purposes = ["Meeting", "Interview", "Consultation", "Review", "Training"]

    for _ in range(count):
        future_time = int(time.time()) + random.randint(3600, 604800)
        appointment_data = {
            "tenant_id": tenant_id,
            "department_id": department_id,
            "host_id": host_id,
            "visitor_name_snapshot": f"Guest {random.randint(1000, 9999)}",
            "scheduled_datetime": future_time,
            "purpose": random.choice(purposes),
            "status": "scheduled",
        }

        response = await client.post(
            f"{BASE_URL}/v1/appointments",
            json=appointment_data,
            headers=headers,
        )

        if response.status_code in (200, 201, 202):
            apt_id = response.json().get("data", {}).get("id")
            appointment_ids.append(apt_id)

    if appointment_ids:
        print(f"✓ Created {len(appointment_ids)} appointments")

    return appointment_ids


async def main():
    """Main setup function."""
    print("\n" + "=" * 60)
    print("Visichek Load Test Data Setup")
    print("=" * 60 + "\n")

    # Verify backend is running
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(f"{BASE_URL}/docs")
            if response.status_code != 200:
                print("ERROR: Backend is not responding correctly")
                sys.exit(1)
        except Exception as e:
            print(f"ERROR: Cannot connect to backend at {BASE_URL}")
            print(f"Make sure the backend is running: {e}")
            sys.exit(1)

    async with httpx.AsyncClient(timeout=30) as client:
        # Creds for the load-test super_admin the locustfile will log in as.
        super_admin_email = os.getenv(
            "LOAD_TEST_EMAIL", "loadtest_super_admin@visichek.com"
        )
        super_admin_password = os.getenv("LOAD_TEST_PASSWORD", "LoadTest@123")

        # Creds for the application admin that bootstraps the tenant.
        # Defaults match the seeded dev superadmin (see seed.py / SUPER_ADMIN_EMAIL).
        admin_email = os.getenv(
            "LOAD_TEST_ADMIN_EMAIL", "superadmin@visicheck.com"
        )
        admin_password = os.getenv(
            "LOAD_TEST_ADMIN_PASSWORD", "@ViViVheck123!"
        )

        # Step 1: Try super_admin login first — if it works, the tenant was
        # already bootstrapped in a previous run and we can skip to data creation.
        print("Step 1: Checking for existing super admin...")
        sa_auth = await login_system_user(
            client, super_admin_email, super_admin_password
        )

        if sa_auth:
            super_admin_headers = sa_auth["headers"]
            super_admin_id = sa_auth["user_id"]
            tenant_id = sa_auth["tenant_id"]
            print(f"✓ Existing super admin found — tenant_id={tenant_id}")
        else:
            print(
                "  No existing super admin — will bootstrap a fresh tenant as application admin"
            )

            # Step 2a: Log in as application admin
            print("\nStep 2a: Logging in as application admin...")
            try:
                admin_auth = await login_admin(client, admin_email, admin_password)
            except Exception as e:
                print(f"✗ Admin login failed: {e}")
                print(
                    "  Ensure an application admin exists with "
                    f"LOAD_TEST_ADMIN_EMAIL={admin_email} / "
                    "LOAD_TEST_ADMIN_PASSWORD=... or seed one via the admin signup flow."
                )
                sys.exit(1)
            print(f"✓ Logged in as application admin ({admin_email})")

            # Step 2b: Bootstrap tenant + first super_admin atomically
            print("\nStep 2b: Bootstrapping tenant + super admin...")
            company_name = f"Load Test Org {int(time.time())}"
            try:
                boot = await bootstrap_tenant(
                    client,
                    admin_auth["headers"],
                    company_name=company_name,
                    admin_full_name="Load Test Super Admin",
                    admin_email=super_admin_email,
                    admin_password=super_admin_password,
                )
            except Exception as e:
                print(f"✗ Bootstrap failed: {e}")
                sys.exit(1)

            tenant_id = boot["tenant_id"]
            super_admin_id = boot["super_admin_id"]
            sa_token = boot["access_token"]
            if not tenant_id or not sa_token:
                print(
                    "✗ Bootstrap succeeded but response was missing tenant_id / access_token"
                )
                sys.exit(1)
            super_admin_headers = {"Authorization": f"Bearer {sa_token}"}
            print(f"✓ Bootstrapped tenant={tenant_id}, super_admin={super_admin_id}")

        # Step 3: Create system users (receptionist, dept_admin) in the tenant
        print("\nStep 3: Creating system users...")
        receptionist_email = os.getenv(
            "LOAD_TEST_RECEPTIONIST_EMAIL", "loadtest_receptionist@visichek.test"
        )
        dept_admin_email = os.getenv(
            "LOAD_TEST_DEPT_ADMIN_EMAIL", "loadtest_dept_admin@visichek.test"
        )

        try:
            await create_system_user(
                client,
                super_admin_headers,
                receptionist_email,
                "Load Test Receptionist",
                "receptionist",
            )
            await create_system_user(
                client,
                super_admin_headers,
                dept_admin_email,
                "Load Test Department Admin",
                "dept_admin",
            )
        except Exception as e:
            print(f"Warning: Failed to create some users: {e}")

        # Step 4: Create departments (async write pipeline — pre-assigned IDs)
        print("\nStep 4: Creating test departments...")
        try:
            department_ids = await create_departments(
                client,
                super_admin_headers,
                tenant_id,
                count=5,
            )
            if not department_ids:
                print("✗ Failed to create any departments")
                sys.exit(1)
        except Exception as e:
            print(f"✗ Failed to create departments: {e}")
            sys.exit(1)

        # Step 5: Create visitor profiles
        print("\nStep 5: Creating visitor profiles...")
        try:
            visitor_ids = await create_visitor_profiles(
                client,
                super_admin_headers,
                tenant_id,
                count=50,
            )
        except Exception as e:
            print(f"Warning: Failed to create all visitor profiles: {e}")
            visitor_ids = []

        # Step 6: Create sample appointments
        print("\nStep 6: Creating sample appointments...")
        if department_ids:
            primary_dept = department_ids[0]
            try:
                await create_appointments(
                    client,
                    super_admin_headers,
                    tenant_id,
                    primary_dept,
                    super_admin_id,
                    count=20,
                )
            except Exception as e:
                print(f"Warning: Failed to create appointments: {e}")

        # Summary
        print("\n" + "=" * 60)
        print("Load Test Data Setup Complete!")
        print("=" * 60)
        print("\nConfiguration:")
        print(f"  Backend URL: {BASE_URL}")
        print(f"  Tenant ID: {tenant_id}")
        print(f"  Department IDs: {department_ids}")
        print(f"  Visitor Profiles Created: {len(visitor_ids)}")

        print("\nSuperAdmin User (for load testing):")
        print(f"  Email: {super_admin_email}")
        print(f"  Password: {super_admin_password}")

        print("\nApplication Admin (for bootstrap tasks):")
        print(f"  Email: {admin_email}")
        print(f"  Password: {admin_password}")

        print("\nOther Users Created:")
        print(f"  Receptionist: {receptionist_email}")
        print(f"  Dept Admin: {dept_admin_email}")

        print("\nTo run load tests with these credentials:")
        print("  cd tests/load")
        print(f"  export LOAD_TEST_EMAIL={super_admin_email}")
        print(f"  export LOAD_TEST_PASSWORD={super_admin_password}")
        print(f"  export LOAD_TEST_ADMIN_EMAIL={admin_email}")
        print(f"  export LOAD_TEST_ADMIN_PASSWORD={admin_password}")
        print(f"  locust -f locustfile.py --host={BASE_URL}")

        print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
