from __future__ import annotations

import asyncio
import os
import sys
import random
import time
from typing import Optional

import httpx

BASE_URL = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")


async def create_tenant(client: httpx.AsyncClient, super_admin_headers: dict) -> str:
    """Create a test tenant."""
    tenant_data = {
        "company_name": f"Load Test Org {int(time.time())}",
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "passive",
        "retention_days": 30,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@loadtest.local",
        "privacy_policy_url": "https://example.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": False,
    }

    response = await client.post(
        f"{BASE_URL}/v1/tenants/",
        json=tenant_data,
        headers=super_admin_headers,
    )

    if response.status_code == 201:
        tenant_id = response.json().get("data", {}).get("id")
        print(f"✓ Created tenant: {tenant_id}")
        return tenant_id
    else:
        raise Exception(
            f"Failed to create tenant: {response.status_code} - {response.text}"
        )


async def create_system_user(
    client: httpx.AsyncClient,
    super_admin_headers: dict,
    tenant_id: str,
    email: str,
    full_name: str,
    role: str,
    department_id: Optional[str] = None,
) -> dict:
    """Create a system user."""
    user_data = {
        "tenant_id": tenant_id,
        "department_id": department_id,
        "full_name": full_name,
        "email": email,
        "password_hash": "LoadTest@123",
        "role": role,
        "account_status": "ACTIVE",
        "is_active": True,
    }

    response = await client.post(
        f"{BASE_URL}/v1/system-users/signup",
        json=user_data,
        headers=super_admin_headers,
    )

    if response.status_code == 201:
        user = response.json().get("data", {})
        print(f"✓ Created {role} user: {email} ({user.get('id')})")
        return user
    else:
        print(f"  Note: User {email} may already exist ({response.status_code})")
        return {}


async def login_user(client: httpx.AsyncClient, email: str, password: str) -> dict:
    """Login and get tokens."""
    login_data = {"email": email, "password": password}

    response = await client.post(
        f"{BASE_URL}/v1/system-users/login",
        json=login_data,
    )

    if response.status_code == 200:
        auth_data = response.json().get("data", {})
        access_token = auth_data.get("access_token")
        return {
            "access_token": access_token,
            "user_id": auth_data.get("id"),
            "headers": {"Authorization": f"Bearer {access_token}"},
        }
    else:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")


async def create_departments(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    created_by: str,
    count: int = 5,
) -> list[str]:
    """Create test departments."""
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

    department_ids = []
    for i in range(min(count, len(dept_names))):
        dept_data = {
            "tenant_id": tenant_id,
            "code": f"DEPT-{i + 1:03d}",
            "name": dept_names[i],
            "is_active": True,
        }

        response = await client.post(
            f"{BASE_URL}/v1/departments/",
            json=dept_data,
            headers=headers,
        )

        if response.status_code == 201:
            dept_id = response.json().get("data", {}).get("id")
            department_ids.append(dept_id)
            print(f"✓ Created department: {dept_names[i]} ({dept_id})")
        else:
            print(
                f"  Failed to create department {dept_names[i]}: {response.status_code}"
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

    if response.status_code == 201:
        return response.json().get("data", {}).get("id")
    else:
        return None


async def create_visitor_profiles(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    count: int = 50,
) -> list[str]:
    """Create multiple visitor profiles in parallel."""
    tasks = [create_visitor_profile(client, headers, tenant_id) for _ in range(count)]

    visitor_ids = []
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
    appointment_ids = []
    purposes = ["Meeting", "Interview", "Consultation", "Review", "Training"]

    for i in range(count):
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

        if response.status_code == 201:
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
        # Step 1: Try to login as super_admin first (for initial setup)
        # If it fails, we'll try to create one
        super_admin_email = "loadtest_super_admin@visichek.test"
        super_admin_password = "LoadTest@123"

        print("Step 1: Setting up super admin authentication...")
        try:
            auth_result = await login_user(
                client, super_admin_email, super_admin_password
            )
            super_admin_headers = auth_result["headers"]
            super_admin_id = auth_result["user_id"]
            print("✓ Logged in as existing super admin")
        except Exception as e:
            print(
                "✗ Super admin login failed, will attempt to create via default admin"
            )
            print("  Note: This requires an existing super admin in the system")
            print(f"  Error: {e}")
            sys.exit(1)

        # Step 2: Create or get tenant
        print("\nStep 2: Creating test tenant...")
        try:
            tenant_id = await create_tenant(client, super_admin_headers)
        except Exception as e:
            print(f"✗ Failed to create tenant: {e}")
            sys.exit(1)

        # Step 3: Create system users (receptionist, dept_admin)
        print("\nStep 3: Creating system users...")
        receptionist_email = "loadtest_receptionist@visichek.test"
        dept_admin_email = "loadtest_dept_admin@visichek.test"

        try:
            await create_system_user(
                client,
                super_admin_headers,
                tenant_id,
                receptionist_email,
                "Load Test Receptionist",
                "receptionist",
            )
            await create_system_user(
                client,
                super_admin_headers,
                tenant_id,
                dept_admin_email,
                "Load Test Department Admin",
                "dept_admin",
            )
        except Exception as e:
            print(f"Warning: Failed to create some users: {e}")

        # Step 4: Create departments
        print("\nStep 4: Creating test departments...")
        try:
            department_ids = await create_departments(
                client,
                super_admin_headers,
                tenant_id,
                super_admin_id,
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

        print("\nOther Users Created:")
        print(f"  Receptionist: {receptionist_email}")
        print(f"  Dept Admin: {dept_admin_email}")

        print("\nTo run load tests with these credentials:")
        print("  cd tests/load")
        print(f"  export LOAD_TEST_EMAIL={super_admin_email}")
        print(f"  export LOAD_TEST_PASSWORD={super_admin_password}")
        print(f"  locust -f locustfile.py --host={BASE_URL}")

        print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
