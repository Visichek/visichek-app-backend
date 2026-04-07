from __future__ import annotations

import os
import random
import string
import time
from typing import Optional
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

        first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emma", "Robert", "Lisa"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]
        companies = ["Acme Corp", "Tech Solutions", "Global Industries", "Innovation Labs", "Future Systems"]
        purposes = ["Business meeting", "Client consultation", "Technical review", "Partnership discussion", "Training session"]

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
                self.active_sessions.append({
                    "id": session_id,
                    "badge_qr_token": badge_token,
                })
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
        params = {
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
        params = {
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
            "purpose": random.choice(["Meeting", "Interview", "Consultation", "Review"]),
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
        params = {
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
        fields = ["visitor_name", "visitor_phone", "id_image", "email_address", "company"]
        payload = {
            "tenant_id": self.tenant_id,
            "field_name": random.choice(fields),
            "purpose": random.choice([
                "Visitor identification",
                "Emergency contact",
                "Compliance audit trail",
                "Access control",
            ]),
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
            "purpose": random.choice([
                "Document storage",
                "Email delivery",
                "SMS notifications",
                "ID verification",
            ]),
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
            "scope": random.choice(["visit_sessions", "id_images", "visitor_profiles", "audit_logs"]),
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
            "incident_type": random.choice([
                "data_breach", "unauthorized_access", "device_loss",
                "misconfiguration", "third_party",
            ]),
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


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when load test starts."""
    logger.info("Load test started")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when load test stops."""
    logger.info("Load test stopped")
    logger.info(f"Total requests: {environment.stats.total.num_requests}")
    logger.info(f"Total failures: {environment.stats.total.num_failures}")
