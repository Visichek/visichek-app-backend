from __future__ import annotations

import os
import random
import time
from typing import Any, Optional
from locust import HttpUser, task, between, events, tag
import logging

logger = logging.getLogger(__name__)


class VisichekLoadUser(HttpUser):
    """
    Locust load test user for Visichek backend.
    Simulates realistic user behavior with check-in/check-out workflows,
    dashboard queries, and appointment management.
    """

    wait_time = between(1, 3)
    host = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers: dict = {}
        self.tenant_id: Optional[str] = None
        self.department_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self.active_sessions: list[dict] = []
        self.system_user_id: Optional[str] = None

    def on_start(self) -> None:
        """Initialize user session: authenticate, create tenant/department."""
        logger.info("Initializing load test user")

        email = os.getenv("LOAD_TEST_EMAIL", "loadtest_super_admin@visichek.test")
        password = os.getenv("LOAD_TEST_PASSWORD", "LoadTest@123")

        login_payload = {
            "email": email,
            "password": password,
        }

        response = self.client.post(
            "/v1/system-users/login",
            json=login_payload,
            name="/v1/system-users/login",
        )

        if response.status_code != 200:
            logger.error(f"Login failed: {response.status_code} - {response.text}")
            raise Exception(f"Failed to login: {response.text}")

        auth_data = response.json().get("data", {})
        access_token = auth_data.get("access_token")
        if not access_token:
            raise Exception("No access token in login response")

        self.headers = {"Authorization": f"Bearer {access_token}"}
        self.system_user_id = auth_data.get("id")
        self.tenant_id = auth_data.get("tenant_id")
        self.user_id = auth_data.get("id")

        logger.info(f"Logged in as {email}, tenant_id={self.tenant_id}")

        # Get or create a test tenant (for super_admin, use their tenant)
        if not self.tenant_id:
            tenant_data = {
                "company_name": f"LoadTest Org {int(time.time())}",
                "lawful_basis": "legitimate_interest",
                "notice_display_mode": "passive",
                "retention_days": 30,
                "default_retention_action": "anonymise",
                "dpo_contact_email": "dpo@loadtest.local",
                "privacy_policy_url": "https://example.com/privacy",
                "country_of_hosting": "United States",
            }
            resp = self.client.post(
                "/v1/tenants/",
                json=tenant_data,
                headers=self.headers,
                name="/v1/tenants/ (create)",
            )
            if resp.status_code == 201:
                self.tenant_id = resp.json().get("data", {}).get("id")
                logger.info(f"Created test tenant: {self.tenant_id}")

        # Create a test department
        dept_data = {
            "tenant_id": self.tenant_id,
            "code": f"LOAD-{random.randint(1000, 9999)}",
            "name": f"Load Test Dept {int(time.time())}",
            "is_active": True,
        }
        resp = self.client.post(
            "/v1/departments/",
            json=dept_data,
            headers=self.headers,
            name="/v1/departments/ (create)",
        )
        if resp.status_code == 201:
            self.department_id = resp.json().get("data", {}).get("id")
            logger.info(f"Created test department: {self.department_id}")
        elif resp.status_code == 200:
            depts = resp.json().get("data", [])
            if depts:
                self.department_id = depts[0].get("id")
                logger.info(f"Using existing department: {self.department_id}")

        if not self.department_id:
            logger.warning("Could not create or retrieve department")

    def on_stop(self) -> None:
        """Clean up: check out any remaining active sessions."""
        logger.info(f"Cleaning up {len(self.active_sessions)} active sessions")
        for session in self.active_sessions:
            try:
                checkout_payload = {
                    "badge_qr_token": session.get("badge_qr_token"),
                    "session_id": session.get("id"),
                }
                self.client.post(
                    "/v1/visitors/check-out",
                    json=checkout_payload,
                    headers=self.headers,
                    name="/v1/visitors/check-out (cleanup)",
                )
            except Exception as e:
                logger.warning(f"Failed to clean up session: {e}")

    @task(10)
    @tag("check_in")
    def check_in_visitor(self) -> None:
        """Check in a new visitor (most common operation)."""
        if not self.department_id:
            return

        first_names = [
            "John",
            "Jane",
            "Michael",
            "Sarah",
            "David",
            "Emma",
            "Robert",
            "Lisa",
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
        ]
        companies = [
            "Acme Corp",
            "Tech Solutions",
            "Global Industries",
            "Innovation Labs",
            "Future Systems",
        ]
        purposes = [
            "Business meeting",
            "Client consultation",
            "Technical review",
            "Partnership discussion",
            "Training session",
        ]

        visitor_name = f"{random.choice(first_names)} {random.choice(last_names)}"
        phone = f"+234{random.randint(8000000000, 8099999999)}"
        company = random.choice(companies)
        purpose = random.choice(purposes)

        check_in_payload = {
            "full_name": visitor_name,
            "phone": phone,
            "company": company,
            "department_id": self.department_id,
            "host_id": self.user_id,
            "purpose": purpose,
            "check_in_method": "manual_entry",
            "consent_granted": True,
        }

        response = self.client.post(
            "/v1/visitors/check-in",
            json=check_in_payload,
            headers=self.headers,
            name="/v1/visitors/check-in",
        )

        if response.status_code == 201:
            session_data = response.json().get("data", {})
            session_id = session_data.get("id")
            badge_token = session_data.get("badge_qr_token")
            if session_id and badge_token:
                self.active_sessions.append(
                    {
                        "id": session_id,
                        "badge_qr_token": badge_token,
                    }
                )
            logger.debug(f"Visitor checked in: {visitor_name}")
        else:
            logger.warning(f"Check-in failed: {response.status_code}")

    @task(8)
    @tag("check_out")
    def check_out_visitor(self) -> None:
        """Check out a visitor if there are active sessions."""
        if not self.active_sessions:
            return

        session = self.active_sessions.pop(0)
        check_out_payload = {
            "badge_qr_token": session.get("badge_qr_token"),
            "session_id": session.get("id"),
            "check_out_method": "qr_scan",
        }

        response = self.client.post(
            "/v1/visitors/check-out",
            json=check_out_payload,
            headers=self.headers,
            name="/v1/visitors/check-out",
        )

        if response.status_code != 200:
            logger.warning(f"Check-out failed: {response.status_code}")

    @task(15)
    @tag("list")
    def list_active_visitors(self) -> None:
        """List currently active visitors (high-frequency operation)."""
        params = {}
        if self.department_id and random.random() > 0.5:
            params["department_id"] = self.department_id

        response = self.client.get(
            "/v1/visitors/active",
            params=params,
            headers=self.headers,
            name="/v1/visitors/active",
        )

        if response.status_code != 200:
            logger.warning(f"List active visitors failed: {response.status_code}")

    @task(5)
    @tag("list")
    def list_visit_sessions(self) -> None:
        """List visitor visit sessions with pagination."""
        params: dict[str, Any] = {
            "start": random.randint(0, 100),
            "stop": random.randint(100, 200),
        }
        if self.department_id and random.random() > 0.5:
            params["department_id"] = self.department_id

        response = self.client.get(
            "/v1/visitors/sessions",
            params=params,
            headers=self.headers,
            name="/v1/visitors/sessions",
        )

        if response.status_code != 200:
            logger.warning(f"List visit sessions failed: {response.status_code}")

    @task(10)
    @tag("dashboard")
    def get_dashboard_stats(self) -> None:
        """Fetch dashboard statistics."""
        params = {}
        if self.department_id and random.random() > 0.5:
            params["department_id"] = self.department_id

        response = self.client.get(
            "/v1/dashboard/stats",
            params=params,
            headers=self.headers,
            name="/v1/dashboard/stats",
        )

        if response.status_code != 200:
            logger.warning(f"Dashboard stats failed: {response.status_code}")

    @task(5)
    @tag("dashboard")
    def get_dashboard_visitors(self) -> None:
        """Fetch dashboard visitor log."""
        params: dict[str, Any] = {
            "start": 0,
            "stop": 50,
        }
        if self.department_id and random.random() > 0.5:
            params["department_id"] = self.department_id

        response = self.client.get(
            "/v1/dashboard/visitors",
            params=params,
            headers=self.headers,
            name="/v1/dashboard/visitors",
        )

        if response.status_code != 200:
            logger.warning(f"Dashboard visitors failed: {response.status_code}")

    @task(3)
    @tag("appointment")
    def create_appointment(self) -> None:
        """Create a new appointment."""
        if not self.department_id:
            return

        future_time = int(time.time()) + random.randint(3600, 86400)
        appointment_data = {
            "tenant_id": self.tenant_id,
            "department_id": self.department_id,
            "host_id": self.user_id,
            "visitor_name": f"Guest {random.randint(100, 999)}",
            "visitor_email": f"guest{random.randint(1000, 9999)}@example.com",
            "visitor_phone": f"+234{random.randint(8000000000, 8099999999)}",
            "appointment_date": future_time,
            "purpose": random.choice(
                ["Meeting", "Interview", "Consultation", "Review"]
            ),
        }

        response = self.client.post(
            "/v1/appointments",
            json=appointment_data,
            headers=self.headers,
            name="/v1/appointments",
        )

        if response.status_code != 201:
            logger.warning(f"Create appointment failed: {response.status_code}")

    @task(2)
    @tag("appointment")
    def list_appointments(self) -> None:
        """List appointments with pagination."""
        params: dict[str, Any] = {
            "start": 0,
            "stop": 50,
        }
        if self.department_id and random.random() > 0.5:
            params["department_id"] = self.department_id

        response = self.client.get(
            "/v1/appointments",
            params=params,
            headers=self.headers,
            name="/v1/appointments",
        )

        if response.status_code != 200:
            logger.warning(f"List appointments failed: {response.status_code}")


class ComplianceLoadUser(HttpUser):
    """
    Locust user simulating DPO / compliance-officer activity:
      - Data Processing Register reads and writes
      - Sub-processor management
      - Retention policy management
      - Incident reporting
      - Audit log reviews
    """

    wait_time = between(2, 5)
    host = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")
    weight = 2  # fewer compliance users relative to visitor flow

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers: dict = {}
        self.tenant_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self._sp_ids: list[str] = []
        self._policy_ids: list[str] = []
        self._incident_ids: list[str] = []

    def on_start(self) -> None:
        email = os.getenv("LOAD_TEST_EMAIL", "loadtest_super_admin@visichek.test")
        password = os.getenv("LOAD_TEST_PASSWORD", "LoadTest@123")

        resp = self.client.post(
            "/v1/system-users/login",
            json={"email": email, "password": password},
            name="/v1/system-users/login (compliance)",
        )
        if resp.status_code != 200:
            raise Exception(f"Compliance user login failed: {resp.text}")

        data = resp.json().get("data", {})
        self.headers = {"Authorization": f"Bearer {data['access_token']}"}
        self.tenant_id = data.get("tenant_id")
        self.user_id = data.get("id")

    # --- DPR ---

    @task(5)
    @tag("compliance", "dpr")
    def list_dpr_register(self) -> None:
        self.client.get(
            "/v1/compliance/register",
            headers=self.headers,
            name="/v1/compliance/register (list)",
        )

    @task(3)
    @tag("compliance", "dpr")
    def add_dpr_entry(self) -> None:
        fields = [
            "visitor_name",
            "visitor_phone",
            "id_image",
            "email_address",
            "company",
        ]
        payload = {
            "tenant_id": self.tenant_id,
            "field_name": random.choice(fields),
            "purpose": random.choice(
                [
                    "Visitor identification",
                    "Emergency contact",
                    "Compliance audit trail",
                    "Access control",
                ]
            ),
            "lawful_basis": random.choice(["consent", "legitimate_interest"]),
            "retention_period": random.choice([90, 180, 365, 730]),
            "crosses_borders": random.choice([True, False]),
        }
        self.client.post(
            "/v1/compliance/register",
            json=payload,
            headers=self.headers,
            name="/v1/compliance/register (create)",
        )

    @task(2)
    @tag("compliance")
    def list_deletion_logs(self) -> None:
        self.client.get(
            "/v1/compliance/deletion-logs",
            headers=self.headers,
            name="/v1/compliance/deletion-logs",
        )

    # --- Sub-Processors ---

    @task(4)
    @tag("compliance", "sub_processor")
    def create_sub_processor(self) -> None:
        providers = ["AWS S3", "Azure Blob", "Google Cloud", "Twilio", "SendGrid"]
        payload = {
            "tenant_id": self.tenant_id,
            "provider": random.choice(providers),
            "purpose": random.choice(
                [
                    "Document storage",
                    "Email delivery",
                    "SMS notifications",
                    "ID verification",
                ]
            ),
            "jurisdiction": random.choice(["EU", "US", "NG", "UK"]),
            "dpa_signed": random.choice([True, False]),
            "uses_data_for_training": False,
        }
        resp = self.client.post(
            "/v1/sub-processors/",
            json=payload,
            headers=self.headers,
            name="/v1/sub-processors/ (create)",
        )
        if resp.status_code in (200, 201):
            sp_id = resp.json().get("data", {}).get("id")
            if sp_id:
                self._sp_ids.append(sp_id)

    @task(5)
    @tag("compliance", "sub_processor")
    def list_sub_processors(self) -> None:
        self.client.get(
            "/v1/sub-processors/",
            headers=self.headers,
            name="/v1/sub-processors/ (list)",
        )

    @task(2)
    @tag("compliance", "sub_processor")
    def update_sub_processor(self) -> None:
        if not self._sp_ids:
            return
        sp_id = random.choice(self._sp_ids)
        self.client.patch(
            f"/v1/sub-processors/{sp_id}",
            json={"dpa_signed": True, "jurisdiction": "EU (Ireland)"},
            headers=self.headers,
            name="/v1/sub-processors/{id} (update)",
        )

    # --- Retention Policies ---

    @task(3)
    @tag("compliance", "retention")
    def create_retention_policy(self) -> None:
        payload = {
            "tenant_id": self.tenant_id,
            "scope": random.choice(
                ["visit_sessions", "id_images", "visitor_profiles", "audit_logs"]
            ),
            "retention_days": random.choice([30, 90, 180, 365, 730]),
            "action": random.choice(["anonymise", "delete"]),
        }
        resp = self.client.post(
            "/v1/retention-policies/",
            json=payload,
            headers=self.headers,
            name="/v1/retention-policies/ (create)",
        )
        if resp.status_code in (200, 201):
            pid = resp.json().get("data", {}).get("id")
            if pid:
                self._policy_ids.append(pid)

    @task(4)
    @tag("compliance", "retention")
    def list_retention_policies(self) -> None:
        self.client.get(
            "/v1/retention-policies/",
            headers=self.headers,
            name="/v1/retention-policies/ (list)",
        )

    # --- Incidents ---

    @task(2)
    @tag("compliance", "incident")
    def create_incident(self) -> None:
        payload = {
            "tenant_id": self.tenant_id,
            "reported_by": self.user_id,
            "incident_type": random.choice(
                [
                    "data_breach",
                    "unauthorized_access",
                    "device_loss",
                    "misconfiguration",
                    "third_party",
                ]
            ),
            "description": f"Load test incident #{random.randint(1, 9999)}",
            "risk_level": random.choice(["low", "medium", "high", "critical"]),
            "detection_time": int(time.time()) - random.randint(60, 7200),
        }
        resp = self.client.post(
            "/v1/incidents/",
            json=payload,
            headers=self.headers,
            name="/v1/incidents/ (create)",
        )
        if resp.status_code in (200, 201):
            iid = resp.json().get("data", {}).get("id")
            if iid:
                self._incident_ids.append(iid)

    @task(4)
    @tag("compliance", "incident")
    def list_incidents(self) -> None:
        self.client.get(
            "/v1/incidents/",
            headers=self.headers,
            name="/v1/incidents/ (list)",
        )

    @task(2)
    @tag("compliance", "incident")
    def update_incident_status(self) -> None:
        if not self._incident_ids:
            return
        iid = random.choice(self._incident_ids)
        self.client.patch(
            f"/v1/incidents/{iid}",
            json={"status": random.choice(["investigating", "contained", "closed"])},
            headers=self.headers,
            name="/v1/incidents/{id} (update)",
        )

    # --- Audit Logs ---

    @task(6)
    @tag("compliance", "audit")
    def list_audit_logs(self) -> None:
        self.client.get(
            "/v1/audit-logs/",
            headers=self.headers,
            name="/v1/audit-logs/ (list)",
        )


class AdminLoadUser(HttpUser):
    """
    Locust user simulating super-admin / dept-admin activity:
      - Super-admin analytics
      - Department management
      - Visitor profile searches
      - Document upload intents
      - Dashboard export
    """

    wait_time = between(2, 6)
    host = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")
    weight = 1  # least common user type

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers: dict = {}
        self.tenant_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self._dept_ids: list[str] = []
        self._doc_ids: list[str] = []

    def on_start(self) -> None:
        email = os.getenv("LOAD_TEST_EMAIL", "loadtest_super_admin@visichek.test")
        password = os.getenv("LOAD_TEST_PASSWORD", "LoadTest@123")

        resp = self.client.post(
            "/v1/system-users/login",
            json={"email": email, "password": password},
            name="/v1/system-users/login (admin)",
        )
        if resp.status_code != 200:
            raise Exception(f"Admin user login failed: {resp.text}")

        data = resp.json().get("data", {})
        self.headers = {"Authorization": f"Bearer {data['access_token']}"}
        self.tenant_id = data.get("tenant_id")
        self.user_id = data.get("id")

    # --- Super Admin ---

    @task(8)
    @tag("admin", "analytics")
    def super_admin_analytics(self) -> None:
        self.client.get(
            "/v1/super-admin/analytics",
            headers=self.headers,
            name="/v1/super-admin/analytics",
        )

    @task(5)
    @tag("admin", "departments")
    def list_all_departments(self) -> None:
        self.client.get(
            "/v1/super-admin/departments",
            headers=self.headers,
            name="/v1/super-admin/departments (list)",
        )

    @task(2)
    @tag("admin", "departments")
    def create_department(self) -> None:
        payload = {
            "tenant_id": self.tenant_id,
            "name": f"LoadDept-{random.randint(1000, 99999)}",
            "description": "Load test department",
            "created_by": self.user_id,
        }
        resp = self.client.post(
            "/v1/super-admin/departments",
            json=payload,
            headers=self.headers,
            name="/v1/super-admin/departments (create)",
        )
        if resp.status_code in (200, 201):
            did = resp.json().get("data", {}).get("id")
            if did:
                self._dept_ids.append(did)

    @task(4)
    @tag("admin")
    def list_all_admins(self) -> None:
        self.client.get(
            "/v1/super-admin/admins",
            headers=self.headers,
            name="/v1/super-admin/admins (list)",
        )

    # --- Visitor Profiles ---

    @task(6)
    @tag("admin", "profiles")
    def list_visitor_profiles(self) -> None:
        self.client.get(
            "/v1/visitor-profiles/",
            headers=self.headers,
            name="/v1/visitor-profiles/ (list)",
        )

    @task(4)
    @tag("admin", "profiles")
    def search_visitor_profiles(self) -> None:
        queries = ["John", "Smith", "Acme", "example.com", "+234"]
        self.client.get(
            "/v1/visitor-profiles/search",
            params={"q": random.choice(queries)},
            headers=self.headers,
            name="/v1/visitor-profiles/search",
        )

    # --- Documents ---

    @task(3)
    @tag("admin", "documents")
    def create_upload_intent(self) -> None:
        payload = {
            "file_name": f"scan_{random.randint(1000, 9999)}.pdf",
            "mime_type": "application/pdf",
            "size": random.randint(10000, 500000),
        }
        resp = self.client.post(
            "/v1/documents/upload-intents",
            json=payload,
            headers=self.headers,
            name="/v1/documents/upload-intents",
        )
        if resp.status_code in (200, 201):
            data = resp.json().get("data", {})
            object_key = data.get("object_key")
            if object_key:
                # Complete the upload
                complete_payload = {
                    "object_key": object_key,
                    "file_name": payload["file_name"],
                    "mime_type": payload["mime_type"],
                    "size": payload["size"],
                }
                c_resp = self.client.post(
                    "/v1/documents/complete",
                    json=complete_payload,
                    headers=self.headers,
                    name="/v1/documents/complete",
                )
                if c_resp.status_code in (200, 201):
                    doc_id = c_resp.json().get("data", {}).get("id")
                    if doc_id:
                        self._doc_ids.append(doc_id)

    @task(2)
    @tag("admin", "documents")
    def get_document(self) -> None:
        if not self._doc_ids:
            return
        doc_id = random.choice(self._doc_ids)
        self.client.get(
            f"/v1/documents/{doc_id}",
            headers=self.headers,
            name="/v1/documents/{id} (get)",
        )

    # --- Dashboard Export ---

    @task(1)
    @tag("admin", "export")
    def dashboard_export_csv(self) -> None:
        self.client.get(
            "/v1/dashboard/export",
            params={"format": "csv"},
            headers=self.headers,
            name="/v1/dashboard/export (csv)",
        )

    @task(1)
    @tag("admin", "export")
    def dashboard_export_xlsx(self) -> None:
        self.client.get(
            "/v1/dashboard/export",
            params={"format": "xlsx"},
            headers=self.headers,
            name="/v1/dashboard/export (xlsx)",
        )

    @task(5)
    @tag("admin", "dashboard")
    def dashboard_active_visitors(self) -> None:
        self.client.get(
            "/v1/dashboard/visitors/active",
            headers=self.headers,
            name="/v1/dashboard/visitors/active",
        )


class BootstrapLoadUser(HttpUser):
    """
    Locust user simulating a application admin bootstrapping tenants.
    Lower weight — this is a rare operation (onboarding new companies).
    Tests the POST /admins/tenants/bootstrap endpoint under load.
    """

    wait_time = between(5, 15)
    host = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")
    weight = 1  # very low frequency

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers: dict = {}
        self._bootstrapped_tenants: list[dict] = []

    def on_start(self) -> None:
        """Authenticate as application admin."""
        email = os.getenv("LOAD_TEST_ADMIN_EMAIL", "loadtest_admin@visichek.test")
        password = os.getenv("LOAD_TEST_ADMIN_PASSWORD", "LoadTestAdmin@123")

        resp = self.client.post(
            "/v1/admins/login",
            json={"email": email, "password": password},
            name="/v1/admins/login (bootstrap)",
        )
        if resp.status_code != 200:
            logger.warning(
                f"Admin login failed ({resp.status_code}), bootstrap tasks will be skipped"
            )
            self.headers = {}
            return

        data = resp.json().get("data", {})
        self.headers = {"Authorization": f"Bearer {data['access_token']}"}

    @task(5)
    @tag("bootstrap")
    def bootstrap_tenant(self) -> None:
        """Bootstrap a new tenant + super_admin."""
        if not self.headers:
            return

        ts = int(time.time())
        rand = random.randint(1000, 99999)
        payload = {
            "company_name": f"LoadTest Corp {ts}_{rand}",
            "lawful_basis": random.choice(["consent", "legitimate_interest"]),
            "notice_display_mode": random.choice(["passive", "active_consent"]),
            "retention_days": random.choice([365, 730, 1095]),
            "dpo_contact_email": f"dpo_{rand}@loadtest.local",
            "country_of_hosting": random.choice(
                ["Nigeria", "United States", "United Kingdom"]
            ),
            "admin_full_name": f"SA {rand}",
            "admin_email": f"sa_{ts}_{rand}@loadtest.local",
            "admin_password": f"LoadPass_{rand}!",
        }

        resp = self.client.post(
            "/v1/admins/tenants/bootstrap",
            json=payload,
            headers=self.headers,
            name="/v1/admins/tenants/bootstrap",
        )
        if resp.status_code in (200, 201):
            data = resp.json().get("data", {})
            self._bootstrapped_tenants.append(
                {
                    "tenant_id": data.get("tenant", {}).get("id"),
                    "sa_token": data.get("super_admin", {}).get("access_token"),
                    "sa_email": payload["admin_email"],
                    "sa_password": payload["admin_password"],
                }
            )
            logger.debug(f"Bootstrapped tenant: {payload['company_name']}")
        else:
            logger.warning(f"Bootstrap failed: {resp.status_code}")

    @task(3)
    @tag("bootstrap", "login")
    def login_bootstrapped_super_admin(self) -> None:
        """Log in as a previously bootstrapped super_admin to verify it works."""
        if not self._bootstrapped_tenants:
            return

        tenant_info = random.choice(self._bootstrapped_tenants)
        resp = self.client.post(
            "/v1/system-users/login",
            json={
                "email": tenant_info["sa_email"],
                "password": tenant_info["sa_password"],
            },
            name="/v1/system-users/login (bootstrapped SA)",
        )
        if resp.status_code != 200:
            logger.warning(f"Bootstrapped SA login failed: {resp.status_code}")

    @task(2)
    @tag("bootstrap", "tenant")
    def create_tenant_as_admin(self) -> None:
        """Create a standalone tenant via POST /tenants/ as application admin."""
        if not self.headers:
            return

        ts = int(time.time())
        rand = random.randint(1000, 99999)
        payload = {
            "company_name": f"Direct Tenant {ts}_{rand}",
            "lawful_basis": "legitimate_interest",
            "notice_display_mode": "passive",
            "retention_days": 730,
        }

        self.client.post(
            "/v1/tenants/",
            json=payload,
            headers=self.headers,
            name="/v1/tenants/ (admin create)",
        )


class BillingLoadUser(HttpUser):
    """
    Locust user simulating billing/SaaS operations (Phase 4):
    - Plan management (list, create, update)
    - Subscription lifecycle (subscribe, list, update, cancel)
    - Invoice retrieval and payment tracking
    - Discount code management
    - Billing summary reports

    Pass/Fail Thresholds:
    - Response time p95 < 1000ms (acceptable for report generation)
    - Response time p99 < 2000ms
    - Error rate < 1% (billing operations are critical)
    - Invoice list/detail endpoints p95 < 500ms
    """

    wait_time = between(2, 5)
    host = os.getenv("LOAD_TEST_HOST", "http://localhost:8000")
    weight = 2  # moderate frequency for SaaS operations

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers: dict = {}
        self.admin_headers: dict = {}
        self.tenant_id: Optional[str] = None
        self.plan_ids: list[str] = []
        self.subscription_ids: list[str] = []
        self.invoice_ids: list[str] = []
        self.discount_ids: list[str] = []

    def on_start(self) -> None:
        """Authenticate as application admin for billing operations."""
        # Try admin first (for plan/billing management)
        admin_email = os.getenv("LOAD_TEST_ADMIN_EMAIL", "loadtest_admin@visichek.test")
        admin_password = os.getenv("LOAD_TEST_ADMIN_PASSWORD", "LoadTestAdmin@123")

        admin_resp = self.client.post(
            "/v1/admins/login",
            json={"email": admin_email, "password": admin_password},
            name="/v1/admins/login (billing)",
        )

        if admin_resp.status_code == 200:
            admin_data = admin_resp.json().get("data", {})
            self.admin_headers = {
                "Authorization": f"Bearer {admin_data.get('access_token')}"
            }
            logger.info("Authenticated as application admin for billing")
        else:
            logger.warning(f"Admin billing auth failed: {admin_resp.status_code}")

        # Also get super_admin/tenant auth if needed
        email = os.getenv("LOAD_TEST_EMAIL", "loadtest_super_admin@visichek.test")
        password = os.getenv("LOAD_TEST_PASSWORD", "LoadTest@123")

        user_resp = self.client.post(
            "/v1/system-users/login",
            json={"email": email, "password": password},
            name="/v1/system-users/login (billing)",
        )

        if user_resp.status_code == 200:
            user_data = user_resp.json().get("data", {})
            self.headers = {"Authorization": f"Bearer {user_data.get('access_token')}"}
            self.tenant_id = user_data.get("tenant_id")

    # --- Plans (Application Admin) ---

    @task(5)
    @tag("billing", "plans")
    def list_plans(self) -> None:
        """List all plans with pagination."""
        if not self.admin_headers:
            return
        self.client.get(
            "/v1/plans/",
            params={"start": 0, "stop": 50},
            headers=self.admin_headers,
            name="/v1/plans/ (list)",
        )

    @task(2)
    @tag("billing", "plans")
    def create_plan(self) -> None:
        """Create a new plan (P95 < 500ms, expected)."""
        if not self.admin_headers:
            return
        ts = int(time.time())
        payload = {
            "name": f"load-plan-{ts}-{random.randint(1000, 9999)}",
            "display_name": f"Load Test Plan {ts}",
            "tier": random.choice(["free", "starter", "professional", "enterprise"]),
            "base_price_monthly": random.choice([0, 50.0, 100.0, 500.0]),
            "base_price_yearly": random.choice([0, 500.0, 1000.0, 5000.0]),
            "status": "active",
        }
        resp = self.client.post(
            "/v1/plans/",
            json=payload,
            headers=self.admin_headers,
            name="/v1/plans/ (create)",
        )
        if resp.status_code == 201:
            plan_id = resp.json().get("data", {}).get("id")
            if plan_id:
                self.plan_ids.append(plan_id)

    @task(3)
    @tag("billing", "plans")
    def get_plan_details(self) -> None:
        """Fetch a specific plan (P95 < 300ms)."""
        if not self.admin_headers or not self.plan_ids:
            return
        plan_id = random.choice(self.plan_ids)
        self.client.get(
            f"/v1/plans/{plan_id}",
            headers=self.admin_headers,
            name="/v1/plans/{id} (get)",
        )

    @task(1)
    @tag("billing", "plans")
    def update_plan(self) -> None:
        """Update an existing plan."""
        if not self.admin_headers or not self.plan_ids:
            return
        plan_id = random.choice(self.plan_ids)
        payload = {
            "base_price_monthly": random.choice([50.0, 75.0, 100.0, 150.0, 200.0]),
            "priority_support": random.choice([True, False]),
        }
        self.client.patch(
            f"/v1/plans/{plan_id}",
            json=payload,
            headers=self.admin_headers,
            name="/v1/plans/{id} (update)",
        )

    # --- Subscriptions ---

    @task(8)
    @tag("billing", "subscriptions")
    def list_subscriptions(self) -> None:
        """List subscriptions for current tenant (P95 < 500ms, expected for aggregate query)."""
        if not self.headers:
            return
        self.client.get(
            "/v1/subscriptions/",
            params={"start": 0, "stop": 50},
            headers=self.headers,
            name="/v1/subscriptions/ (list)",
        )

    @task(2)
    @tag("billing", "subscriptions")
    def subscribe_to_plan(self) -> None:
        """Subscribe a tenant to a plan."""
        if not self.headers or not self.admin_headers or not self.plan_ids:
            return
        # Admin creates subscription for tenant
        plan_id = random.choice(self.plan_ids)
        payload = {
            "tenant_id": self.tenant_id,
            "plan_id": plan_id,
            "billing_cycle": random.choice(["monthly", "yearly"]),
            "status": "active",
        }
        resp = self.client.post(
            "/v1/subscriptions/",
            json=payload,
            headers=self.admin_headers,
            name="/v1/subscriptions/ (create)",
        )
        if resp.status_code == 201:
            sub_id = resp.json().get("data", {}).get("id")
            if sub_id:
                self.subscription_ids.append(sub_id)

    @task(4)
    @tag("billing", "subscriptions")
    def get_subscription_details(self) -> None:
        """Fetch subscription details (P95 < 300ms)."""
        if not self.headers or not self.subscription_ids:
            return
        sub_id = random.choice(self.subscription_ids)
        self.client.get(
            f"/v1/subscriptions/{sub_id}",
            headers=self.headers,
            name="/v1/subscriptions/{id} (get)",
        )

    @task(2)
    @tag("billing", "subscriptions")
    def update_subscription(self) -> None:
        """Update subscription status or billing cycle."""
        if not self.headers or not self.subscription_ids:
            return
        sub_id = random.choice(self.subscription_ids)
        payload = {
            "status": random.choice(["active", "trialing", "past_due"]),
        }
        self.client.patch(
            f"/v1/subscriptions/{sub_id}",
            json=payload,
            headers=self.headers,
            name="/v1/subscriptions/{id} (update)",
        )

    @task(1)
    @tag("billing", "subscriptions")
    def cancel_subscription(self) -> None:
        """Cancel a subscription."""
        if not self.headers or not self.subscription_ids:
            return
        sub_id = self.subscription_ids.pop(0)
        payload = {
            "status": "cancelled",
            "cancellation_reason": "Load test cancellation",
        }
        self.client.patch(
            f"/v1/subscriptions/{sub_id}",
            json=payload,
            headers=self.headers,
            name="/v1/subscriptions/{id} (cancel)",
        )

    # --- Invoices ---

    @task(6)
    @tag("billing", "invoices")
    def list_invoices(self) -> None:
        """List invoices for tenant (P95 < 500ms, may involve aggregation)."""
        if not self.headers:
            return
        self.client.get(
            "/v1/invoices/",
            params={"start": 0, "stop": 50},
            headers=self.headers,
            name="/v1/invoices/ (list)",
        )

    @task(3)
    @tag("billing", "invoices")
    def get_invoice_details(self) -> None:
        """Fetch a specific invoice (P95 < 300ms)."""
        if not self.headers or not self.invoice_ids:
            return
        invoice_id = random.choice(self.invoice_ids)
        self.client.get(
            f"/v1/invoices/{invoice_id}",
            headers=self.headers,
            name="/v1/invoices/{id} (get)",
        )

    @task(2)
    @tag("billing", "invoices")
    def download_invoice_pdf(self) -> None:
        """Download invoice PDF (P95 < 1000ms, includes file generation)."""
        if not self.headers or not self.invoice_ids:
            return
        invoice_id = random.choice(self.invoice_ids)
        self.client.get(
            f"/v1/invoices/{invoice_id}/pdf",
            headers=self.headers,
            name="/v1/invoices/{id}/pdf (download)",
        )

    # --- Discounts ---

    @task(3)
    @tag("billing", "discounts")
    def list_discounts(self) -> None:
        """List discount codes (P95 < 300ms)."""
        if not self.admin_headers:
            return
        self.client.get(
            "/v1/discounts/",
            params={"start": 0, "stop": 50},
            headers=self.admin_headers,
            name="/v1/discounts/ (list)",
        )

    @task(1)
    @tag("billing", "discounts")
    def create_discount(self) -> None:
        """Create a new discount code."""
        if not self.admin_headers:
            return
        ts = int(time.time())
        payload = {
            "code": f"LOAD-{ts}-{random.randint(100, 999)}",
            "name": f"Load Test Discount {ts}",
            "discount_type": random.choice(["percentage", "fixed"]),
            "value": random.choice([5.0, 10.0, 25.0, 50.0])
            if random.random() > 0.5
            else random.choice([100, 500, 1000]),
            "scope": "global",
            "max_redemptions": random.choice([10, 50, 100, 500]),
        }
        resp = self.client.post(
            "/v1/discounts/",
            json=payload,
            headers=self.admin_headers,
            name="/v1/discounts/ (create)",
        )
        if resp.status_code == 201:
            disc_id = resp.json().get("data", {}).get("id")
            if disc_id:
                self.discount_ids.append(disc_id)

    # --- Billing Reports ---

    @task(2)
    @tag("billing", "reports")
    def get_billing_summary(self) -> None:
        """Get billing summary report (P95 < 1000ms, may involve complex aggregation)."""
        if not self.admin_headers:
            return
        now = int(time.time())
        start = now - 2592000  # 30 days ago
        self.client.get(
            "/v1/billing/summary",
            params={"start": start, "end": now},
            headers=self.admin_headers,
            name="/v1/billing/summary (report)",
        )

    @task(1)
    @tag("billing", "reports")
    def get_payment_discrepancies(self) -> None:
        """Check for payment discrepancies (P95 < 2000ms, heavy aggregation)."""
        if not self.admin_headers:
            return
        self.client.get(
            "/v1/billing/discrepancies",
            headers=self.admin_headers,
            name="/v1/billing/discrepancies (detect)",
        )

    # --- Health Checks ---

    @task(10)
    @tag("health")
    def health_check(self) -> None:
        """Frequent health checks (P95 < 100ms, should be instant)."""
        self.client.get("/health", name="/health")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when load test starts."""
    logger.info("Load test started")
    logger.info("Load test configuration:")
    logger.info("  - BillingLoadUser: SaaS billing operations")
    logger.info(
        "    P95 thresholds: Plans/Subs <500ms, Invoices <500ms, Reports <1000ms"
    )
    logger.info("    Error rate threshold: <1% (billing is critical)")
    logger.info("  - Health checks should complete in <100ms")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when load test stops."""
    logger.info("Load test stopped")
    logger.info(f"Total requests: {environment.stats.total.num_requests}")
    logger.info(f"Total failures: {environment.stats.total.num_failures}")
    logger.info(
        f"Failure rate: {(environment.stats.total.num_failures / environment.stats.total.num_requests * 100):.2f}%"
    )
    logger.info("Performance thresholds:")
    logger.info("  - Health checks (p95): <100ms")
    logger.info("  - Plan/Subscription operations (p95): <500ms")
    logger.info("  - Invoice retrieval (p95): <300ms")
    logger.info("  - Billing reports (p95): <1000ms")
    logger.info("  - Payment discrepancy detection (p95): <2000ms")
    logger.info("  - Overall error rate: Must be <1% for production readiness")
