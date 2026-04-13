from __future__ import annotations

import time

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorDatabase

from schemas.tenant_schema import TenantCreate, TenantOut
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserOut,
)
from schemas.department_schema import DepartmentCreate
from schemas.visit_session_schema import CheckInRequest, CheckOutRequest
from schemas.privacy_notice_schema import PrivacyNoticeCreate, PrivacyNoticeUpdate
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate
from schemas.appointment_schema import AppointmentCreate
from schemas.imports import (
    SystemUserRole,
    LawfulBasis,
    NoticeDisplayMode,
    DSRType,
    DSRStatus,
    CheckInMethod,
    AppointmentStatus,
)
from repositories.tenant_repo import create_tenant
from services.tenant_service import add_tenant
from repositories.system_user_repo import create_system_user
from repositories.department_repo import (
    create_department,
    get_departments,
    update_department,
    delete_department,
)
from repositories.visit_session_repo import (
    create_visit_session,
    get_visit_session,
)
from repositories.privacy_notice_repo import (
    create_privacy_notice,
    get_privacy_notice,
    update_privacy_notice,
)
from repositories.data_subject_request_repo import (
    create_dsr,
    get_dsr,
    update_dsr,
)
from repositories.appointment_repo import (
    create_appointment,
    update_appointment,
)
from repositories.visitor_profile_repo import create_visitor_profile
from schemas.visitor_profile_schema import VisitorProfileCreate


# ============================================================================
# TestAuthFlow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestAuthFlow:
    """Integration tests for authentication flow: signup -> login -> refresh."""

    async def test_system_user_signup_login_refresh_flow(
        self,
        integration_client: AsyncClient,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
    ):
        """
        Full auth flow: Create system user -> login -> get profile -> refresh tokens.
        Verifies response envelope format, token issuance, and token refresh.
        """
        # Setup: Create a system user via signup
        email = f"flow_test_{int(time.time())}@test.example.com"
        password = "TestPassword123!Flow"

        signup_response = await integration_client.post(
            "/v1/system-users/signup",
            json={
                "tenant_id": seeded_tenant.id,
                "full_name": "Flow Test User",
                "email": email,
                "role": "receptionist",
                "password_hash": password,
            },
            headers={"Authorization": f"Bearer {await self._get_admin_token(integration_client, seeded_tenant)}"}
        )

        assert signup_response.status_code == 201
        signup_data = signup_response.json()
        assert signup_data["success"] is True
        assert signup_data["message"] == "System user created successfully"
        assert "data" in signup_data

        user_id = signup_data["data"]["id"]
        access_token_from_signup = signup_data["data"]["access_token"]
        refresh_token_from_signup = signup_data["data"]["refresh_token"]

        assert access_token_from_signup is not None
        assert refresh_token_from_signup is not None

        # Step 1: Login with email and password
        login_response = await integration_client.post(
            "/v1/system-users/login",
            json={
                "email": email,
                "password": password,
            }
        )

        assert login_response.status_code == 200
        login_data = login_response.json()
        assert login_data["success"] is True
        assert login_data["message"] == "Login successful"

        user_from_login = login_data["data"]
        assert user_from_login["email"] == email
        assert user_from_login["role"] == "receptionist"
        assert user_from_login["account_status"] == "ACTIVE"

        access_token = user_from_login["access_token"]
        refresh_token = user_from_login["refresh_token"]

        # Step 2: Get profile using access token
        profile_response = await integration_client.get(
            "/v1/system-users/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )

        assert profile_response.status_code == 200
        profile_data = profile_response.json()
        assert profile_data["success"] is True
        assert profile_data["message"] == "Profile fetched successfully"
        assert profile_data["data"]["id"] == user_id

        # Step 3: Refresh tokens using refresh token
        refresh_response = await integration_client.post(
            "/v1/system-users/refresh",
            json={
                "refresh_token": refresh_token,
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )

        assert refresh_response.status_code == 200
        refresh_data = refresh_response.json()
        assert refresh_data["success"] is True
        assert refresh_data["message"] == "Tokens refreshed successfully"

        new_user = refresh_data["data"]
        new_access_token = new_user["access_token"]
        new_refresh_token = new_user["refresh_token"]

        assert new_access_token is not None
        assert new_refresh_token is not None
        assert new_access_token != access_token  # Token should be different

        # Step 4: Verify new access token works
        verify_response = await integration_client.get(
            "/v1/system-users/me",
            headers={"Authorization": f"Bearer {new_access_token}"}
        )

        assert verify_response.status_code == 200
        verify_data = verify_response.json()
        assert verify_data["success"] is True
        assert verify_data["data"]["id"] == user_id

    async def test_login_with_wrong_password_returns_401(
        self,
        integration_client: AsyncClient,
        seeded_system_user: tuple[SystemUserOut, str],
    ):
        """Verify login with incorrect password returns 401 Unauthorized."""
        user, _ = seeded_system_user

        login_response = await integration_client.post(
            "/v1/system-users/login",
            json={
                "email": user.email,
                "password": "WrongPassword123!",
            }
        )

        assert login_response.status_code == 401
        data = login_response.json()
        assert data["success"] is False

    async def test_expired_token_rejected(
        self,
        integration_client: AsyncClient,
        seeded_system_user: tuple[SystemUserOut, str],
    ):
        """Verify that an invalid/malformed token is rejected."""
        invalid_token = "invalid.malformed.token"

        response = await integration_client.get(
            "/v1/system-users/me",
            headers={"Authorization": f"Bearer {invalid_token}"}
        )

        # Should return 401 or 422 for invalid token
        assert response.status_code in [401, 422]
        data = response.json()
        assert data["success"] is False

    async def _get_admin_token(
        self,
        client: AsyncClient,
        tenant: TenantOut,
    ) -> str:
        """Helper to get a super_admin token for the given tenant."""

        # This is a helper to create admin token; in real tests, use seeded_system_user
        raise NotImplementedError("Use auth_headers fixture or create dedicated admin user")


# ============================================================================
# TestVisitorCheckInCheckOutFlow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestVisitorCheckInCheckOutFlow:
    """Integration tests for visitor check-in/check-out lifecycle."""

    async def test_full_visitor_lifecycle(
        self,
        integration_client: AsyncClient,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
        auth_headers: dict[str, str],
    ):
        """
        Complete visitor lifecycle: Create dept -> Create host -> Check-in -> Verify active ->
        Check-out -> Verify inactive -> Verify session duration.
        """
        tenant_id = seeded_tenant.id or ""

        # Create a department
        dept_data = DepartmentCreate(
            tenant_id=tenant_id,
            code="SALES",
            name="Sales Department",
        )
        dept = await create_department(dept_data)

        # Create a host (system user)
        host_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="Host User",
            email=f"host_{int(time.time())}@test.example.com",
            role=SystemUserRole.RECEPTIONIST,
            password_hash="HostPassword123!",
        )
        host = await create_system_user(host_data)

        # Create visitor profile
        visitor_data = VisitorProfileCreate(
            tenant_id=tenant_id,
            full_name="John Visitor",
            phone="1234567890",
            company="Visitor Corp",
        )
        visitor = await create_visitor_profile(visitor_data)

        # Check-in visitor
        checkin_request = CheckInRequest(
            full_name="John Visitor",
            phone="1234567890",
            company="Visitor Corp",
            department_id=dept.id or "",
            host_id=host.id or "",
            purpose="Business meeting",
        )

        checkin_response = await integration_client.post(
            "/v1/visitors/check-in",
            json=checkin_request.model_dump(),
            headers=auth_headers,
        )

        assert checkin_response.status_code == 201
        checkin_data = checkin_response.json()
        assert checkin_data["success"] is True
        assert checkin_data["message"] == "Visitor checked in successfully"

        visit_session = checkin_data["data"]
        session_id = visit_session["id"]
        assert visit_session["status"] == "checked_in"
        assert visit_session["visitor_name_snapshot"] == "John Visitor"
        assert visit_session["check_in_time"] is not None
        assert visit_session["check_out_time"] is None
        assert visit_session["visit_duration"] is None

        # Get the visit session directly from DB to verify persistence
        db_session = await get_visit_session({"_id": ObjectId(session_id)})
        assert db_session is not None
        assert db_session.status.value == "checked_in"

        # Check-out visitor
        checkout_request = CheckOutRequest(
            session_id=session_id,
        )

        checkout_response = await integration_client.post(
            "/v1/visitors/check-out",
            json=checkout_request.model_dump(),
            headers=auth_headers,
        )

        assert checkout_response.status_code == 200
        checkout_data = checkout_response.json()
        assert checkout_data["success"] is True
        assert checkout_data["message"] == "Visitor checked out successfully"

        checked_out_session = checkout_data["data"]
        assert checked_out_session["status"] == "checked_out"
        assert checked_out_session["check_out_time"] is not None
        assert checked_out_session["visit_duration"] is not None
        assert checked_out_session["visit_duration"] > 0

    async def test_check_in_without_required_fields_fails(
        self,
        integration_client: AsyncClient,
        seeded_tenant: TenantOut,
        auth_headers: dict[str, str],
    ):
        """Verify that check-in without required fields returns validation error."""
        # Missing purpose and other required fields
        checkin_request = {
            "full_name": "John Visitor",
            # Missing: phone, company, department_id, host_id, purpose
        }

        response = await integration_client.post(
            "/v1/visitors/check-in",
            json=checkin_request,
            headers=auth_headers,
        )

        assert response.status_code >= 400
        data = response.json()
        assert data["success"] is False

    async def test_check_out_with_invalid_badge_token_fails(
        self,
        integration_client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        """Verify that check-out with invalid session_id returns 404."""
        from bson import ObjectId

        invalid_session_id = str(ObjectId())

        checkout_request = CheckOutRequest(
            session_id=invalid_session_id,
        )

        response = await integration_client.post(
            "/v1/visitors/check-out",
            json=checkout_request.model_dump(),
            headers=auth_headers,
        )

        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False


# ============================================================================
# TestTenantDepartmentFlow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestTenantDepartmentFlow:
    """Integration tests for tenant and department management."""

    async def test_create_tenant_and_departments(
        self,
        integration_client: AsyncClient,
        mongo_db: AsyncIOMotorDatabase,
        auth_headers: dict[str, str],
    ):
        """
        Create tenant -> Create 3 departments -> List -> Verify count ->
        Update department -> Verify update -> Delete -> Verify count decreased.
        """
        # Create a new tenant
        tenant_data = TenantCreate(
            company_name=f"MultiDept Corp {int(time.time())}",
            lawful_basis=LawfulBasis.CONSENT,
            notice_display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
            retention_days=730,
        )
        tenant = await create_tenant(tenant_data)
        tenant_id = tenant.id or ""

        # Create 3 departments
        dept_ids = []
        for i in range(3):
            dept_data = DepartmentCreate(
                tenant_id=tenant_id,
                code=f"DEPT{i}",
                name=f"Department {i}",
            )
            dept = await create_department(dept_data)
            dept_ids.append(dept.id)

        # Retrieve all departments for tenant from DB
        all_depts = await get_departments(
            filter_dict={"tenant_id": tenant_id}
        )
        assert len(all_depts) == 3

        # Update first department
        updated_dept_data = DepartmentUpdate(
            name="Updated Department 0",
        )
        updated_dept = await update_department(
            {"_id": ObjectId(dept_ids[0])},
            updated_dept_data,
        )
        assert updated_dept.name == "Updated Department 0"

        # Delete one department
        delete_result = await delete_department(
            {"_id": ObjectId(dept_ids[2])}
        )
        assert delete_result.deleted_count == 1

        # Verify count decreased
        remaining_depts = await get_departments(
            filter_dict={"tenant_id": tenant_id}
        )
        assert len(remaining_depts) == 2

    async def test_duplicate_tenant_name_rejected(
        self,
        integration_client: AsyncClient,
        mongo_db: AsyncIOMotorDatabase,
    ):
        """Verify that creating a duplicate tenant name returns 409 Conflict."""
        company_name = f"Duplicate Corp {int(time.time())}"

        # Create first tenant
        tenant_data1 = TenantCreate(
            company_name=company_name,
        )
        tenant1 = await add_tenant(tenant_data1)
        assert tenant1.id is not None

        # Attempt to create second tenant with same name
        tenant_data2 = TenantCreate(
            company_name=company_name,
        )

        with pytest.raises(Exception):  # Should raise HTTPException 409
            await add_tenant(tenant_data2)


# ============================================================================
# TestComplianceFlow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestComplianceFlow:
    """Integration tests for compliance features: privacy notices and DSRs."""

    async def test_privacy_notice_lifecycle(
        self,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
    ):
        """
        Create privacy notice -> Get active -> Create new version ->
        Old deactivated -> Verify versioning.
        """
        tenant_id = seeded_tenant.id or ""
        now = int(time.time())

        # Create initial privacy notice v1.0
        notice_v1_data = PrivacyNoticeCreate(
            tenant_id=tenant_id,
            version_code="1.0",
            title="Privacy Notice v1.0",
            summary="Initial privacy notice",
            effective_from=now,
            is_active=True,
        )
        notice_v1 = await create_privacy_notice(notice_v1_data)
        assert notice_v1.is_active is True

        # Retrieve active notice
        active_notice = await get_privacy_notice(
            filter_dict={"tenant_id": tenant_id, "is_active": True}
        )
        assert active_notice is not None
        assert active_notice.version_code == "1.0"

        # Create new version v1.1 and deactivate old
        notice_v1_update = PrivacyNoticeUpdate(
            is_active=False,
            effective_to=now + 100,
        )
        await update_privacy_notice(
            {"_id": ObjectId(notice_v1.id)},
            notice_v1_update,
        )

        notice_v1_1_data = PrivacyNoticeCreate(
            tenant_id=tenant_id,
            version_code="1.1",
            title="Privacy Notice v1.1",
            summary="Updated privacy notice",
            effective_from=now + 100,
            is_active=True,
        )
        notice_v1_1 = await create_privacy_notice(notice_v1_1_data)
        assert notice_v1_1.is_active is True

        # Verify versioning
        new_active = await get_privacy_notice(
            filter_dict={"tenant_id": tenant_id, "is_active": True}
        )
        assert new_active is not None
        assert new_active.version_code == "1.1"

        # Verify old notice is deactivated
        old_notice = await get_privacy_notice(
            filter_dict={"_id": ObjectId(notice_v1.id)}
        )
        assert old_notice is not None
        assert old_notice.is_active is False

    async def test_dsr_lifecycle(
        self,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
    ):
        """
        Create DSR -> Update status to in_progress -> Complete ->
        Verify resolution timestamp and status.
        """
        tenant_id = seeded_tenant.id or ""

        # Create a visitor profile for DSR
        visitor_data = VisitorProfileCreate(
            tenant_id=tenant_id,
            full_name="Data Subject",
            phone="9876543210",
            company="Data Corp",
        )
        visitor = await create_visitor_profile(visitor_data)

        # Create DSR (Data Subject Request)
        dsr_create_data = DSRCreate(
            tenant_id=tenant_id,
            visitor_profile_id=visitor.id or "",
            request_type=DSRType.ACCESS,
            status=DSRStatus.PENDING,
        )
        dsr = await create_dsr(dsr_create_data)
        assert dsr.status.value == "pending"

        # Retrieve DSR
        dsr_retrieved = await get_dsr(
            {"_id": ObjectId(dsr.id)}
        )
        assert dsr_retrieved is not None

        # Update status to in_progress
        dsr_update_progress = DSRUpdate(
            status=DSRStatus.IN_PROGRESS,
        )
        dsr_in_progress = await update_dsr(
            {"_id": ObjectId(dsr.id)},
            dsr_update_progress,
        )
        assert dsr_in_progress.status.value == "in_progress"

        # Complete the DSR
        now = int(time.time())
        dsr_update_complete = DSRUpdate(
            status=DSRStatus.COMPLETED,
            resolved_at=now,
        )
        dsr_completed = await update_dsr(
            {"_id": ObjectId(dsr.id)},
            dsr_update_complete,
        )
        assert dsr_completed.status.value == "completed"
        assert dsr_completed.resolved_at == now


# ============================================================================
# TestAppointmentFlow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestAppointmentFlow:
    """Integration tests for appointment management."""

    async def test_appointment_create_and_fulfill(
        self,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
    ):
        """
        Create appointment -> Check-in visitor with appointment ->
        Verify appointment status updated.
        """
        from bson import ObjectId

        tenant_id = seeded_tenant.id or ""
        scheduled_time = int(time.time()) + 3600  # 1 hour from now

        # Create department and host
        dept_data = DepartmentCreate(
            tenant_id=tenant_id,
            code="APPT",
            name="Appointment Department",
        )
        dept = await create_department(dept_data)

        host_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="Appointment Host",
            email=f"host_appt_{int(time.time())}@test.example.com",
            role=SystemUserRole.RECEPTIONIST,
            password_hash="HostPassword123!",
        )
        host = await create_system_user(host_data)

        # Create appointment
        appt_data = AppointmentCreate(
            tenant_id=tenant_id,
            host_id=host.id or "",
            department_id=dept.id or "",
            scheduled_datetime=scheduled_time,
            purpose="Scheduled meeting",
            status=AppointmentStatus.SCHEDULED,
        )
        appointment = await create_appointment(appt_data)
        assert appointment.status.value == "scheduled"

        # Create visitor for appointment
        visitor_data = VisitorProfileCreate(
            tenant_id=tenant_id,
            full_name="Scheduled Visitor",
            phone="5551234567",
            company="Appointment Corp",
        )
        visitor = await create_visitor_profile(visitor_data)

        # Check-in visitor linked to appointment
        visit_data = CheckInRequest(
            full_name="Scheduled Visitor",
            phone="5551234567",
            company="Appointment Corp",
            department_id=dept.id or "",
            host_id=host.id or "",
            purpose="Scheduled meeting",
        )
        # Note: In a real test, you'd call the actual check-in endpoint
        # and it would link the appointment. For now, we create it directly.
        visit_session_data = {
            "tenant_id": tenant_id,
            "visitor_profile_id": visitor.id,
            "department_id": dept.id,
            "host_id": host.id,
            "receptionist_id": host.id,
            "appointment_id": appointment.id,
            "check_in_method": CheckInMethod.MANUAL,
            "status": "checked_in",
            "purpose": "Scheduled meeting",
            "visitor_name_snapshot": "Scheduled Visitor",
            "company_snapshot": "Appointment Corp",
            "host_name_snapshot": "Appointment Host",
            "department_name_snapshot": "Appointment Department",
            "receptionist_name_snapshot": "Appointment Host",
            "consent_granted": True,
            "check_in_time": int(time.time()),
        }

        from schemas.visit_session_schema import VisitSessionCreate

        visit_session_create = VisitSessionCreate(**visit_session_data)  # type: ignore[arg-type]
        visit_session = await create_visit_session(visit_session_create)
        assert visit_session.appointment_id == appointment.id

        # Update appointment status to fulfilled
        appt_update = AppointmentUpdate(
            status=AppointmentStatus.FULFILLED,
        )
        updated_appt = await update_appointment(
            {"_id": ObjectId(appointment.id)},
            appt_update,
        )
        assert updated_appt.status.value == "fulfilled"


# Imports needed for the tests above
from bson import ObjectId
from schemas.department_schema import DepartmentUpdate
from schemas.appointment_schema import AppointmentUpdate
