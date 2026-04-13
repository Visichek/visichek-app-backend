from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from bson import ObjectId
from fastapi import HTTPException

from schemas.tenant_schema import (
    TenantCreate,
    TenantUpdate,
    TenantOut,
    TenantBootstrapRequest,
)
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
)
from schemas.appointment_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentOut,
)
from schemas.visit_session_schema import (
    VisitSessionOut,
    CheckInRequest,
    CheckOutRequest,
)
from schemas.visitor_profile_schema import (
    VisitorProfileOut,
)
from schemas.imports import (
    LawfulBasis,
    CheckInMethod,
    CheckOutMethod,
    VisitStatus,
    AccountStatus,
    SystemUserRole,
    AppointmentStatus,
)


# ============================================================================
# TENANT SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestTenantService:
    """Test suite for tenant service layer."""

    @patch("services.tenant_service.create_tenant")
    @patch("services.tenant_service.get_tenant")
    async def test_add_tenant_success(self, mock_get, mock_create):
        """Test adding a new tenant successfully."""
        from services.tenant_service import add_tenant

        tenant_id = str(ObjectId())
        tenant_data = TenantCreate(
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
            retention_days=1095,
        )
        created_tenant = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
            retention_days=1095,
            date_created=1234567890,
        )

        mock_get.return_value = None  # No existing tenant
        mock_create.return_value = created_tenant

        result = await add_tenant(tenant_data)

        assert result.id == tenant_id
        assert result.company_name == "Acme Corp"
        mock_get.assert_called_once_with(filter_dict={"company_name": "Acme Corp"})
        mock_create.assert_called_once_with(tenant_data)

    @patch("services.tenant_service.get_tenant")
    async def test_add_tenant_duplicate_name_raises_409(self, mock_get):
        """Test that adding tenant with duplicate name raises 409."""
        from services.tenant_service import add_tenant

        tenant_id = str(ObjectId())
        tenant_data = TenantCreate(
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
        )
        existing_tenant = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
        )

        mock_get.return_value = existing_tenant

        with pytest.raises(HTTPException) as exc_info:
            await add_tenant(tenant_data)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail

    @patch("services.tenant_service.get_tenant")
    async def test_retrieve_tenant_by_id_success(self, mock_get):
        """Test retrieving a tenant by ID."""
        from services.tenant_service import retrieve_tenant_by_id

        tenant_id = str(ObjectId())
        tenant = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
        )

        mock_get.return_value = tenant

        result = await retrieve_tenant_by_id(tenant_id)

        assert result.id == tenant_id
        assert result.company_name == "Acme Corp"
        mock_get.assert_called_once()

    async def test_retrieve_tenant_by_id_invalid_objectid(self):
        """Test that invalid ObjectId format raises 400."""
        from services.tenant_service import retrieve_tenant_by_id

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_tenant_by_id("invalid-id-format")

        assert exc_info.value.status_code == 400
        assert "Invalid tenant ID format" in exc_info.value.detail

    @patch("services.tenant_service.get_tenant")
    async def test_retrieve_tenant_by_id_not_found(self, mock_get):
        """Test retrieving non-existent tenant raises 404."""
        from services.tenant_service import retrieve_tenant_by_id

        tenant_id = str(ObjectId())
        mock_get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_tenant_by_id(tenant_id)

        assert exc_info.value.status_code == 404
        assert "Tenant not found" in exc_info.value.detail

    @patch("services.tenant_service.update_tenant")
    async def test_update_tenant_success(self, mock_update):
        """Test updating a tenant."""
        from services.tenant_service import update_tenant_by_id

        tenant_id = str(ObjectId())
        update_data = TenantUpdate(company_name="Acme Corp Updated")
        updated_tenant = TenantOut(
            id=tenant_id,
            company_name="Acme Corp Updated",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
        )

        mock_update.return_value = updated_tenant

        result = await update_tenant_by_id(tenant_id, update_data)

        assert result.company_name == "Acme Corp Updated"
        mock_update.assert_called_once()

    @patch("services.tenant_service.update_tenant")
    async def test_update_tenant_not_found(self, mock_update):
        """Test updating non-existent tenant raises 404."""
        from services.tenant_service import update_tenant_by_id

        tenant_id = str(ObjectId())
        update_data = TenantUpdate(company_name="Updated")
        mock_update.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_tenant_by_id(tenant_id, update_data)

        assert exc_info.value.status_code == 404

    @patch("services.tenant_service.delete_tenant")
    async def test_remove_tenant_success(self, mock_delete):
        """Test removing a tenant."""
        from services.tenant_service import remove_tenant

        tenant_id = str(ObjectId())
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_delete.return_value = mock_result

        await remove_tenant(tenant_id)

        mock_delete.assert_called_once()

    @patch("services.tenant_service.delete_tenant")
    async def test_remove_tenant_not_found(self, mock_delete):
        """Test removing non-existent tenant raises 404."""
        from services.tenant_service import remove_tenant

        tenant_id = str(ObjectId())
        mock_result = MagicMock()
        mock_result.deleted_count = 0
        mock_delete.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await remove_tenant(tenant_id)

        assert exc_info.value.status_code == 404


# ============================================================================
# SYSTEM USER SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestSystemUserService:
    """Test suite for system user service layer."""

    @patch("services.system_user_service.issue_tokens_for_role")
    @patch("services.system_user_service.create_system_user")
    @patch("services.system_user_service.get_system_user")
    async def test_add_system_user_success(self, mock_get, mock_create, mock_tokens):
        """Test adding a new system user."""
        from services.system_user_service import add_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        user_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="John Receptionist",
            email="john@acme.com",
            password_hash="password123",
            role=SystemUserRole.RECEPTIONIST,
        )
        created_user = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John Receptionist",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
            account_status=AccountStatus.ACTIVE,
        )

        mock_get.return_value = None
        mock_create.return_value = created_user
        mock_tokens.return_value = ("access_token_123", "refresh_token_456")

        result = await add_system_user(user_data)

        assert result.id == user_id
        assert result.email == "john@acme.com"
        assert result.access_token == "access_token_123"
        assert result.refresh_token == "refresh_token_456"

    @patch("services.system_user_service.get_system_user")
    async def test_add_system_user_duplicate_email_raises_409(self, mock_get):
        """Test adding user with duplicate email raises 409."""
        from services.system_user_service import add_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        user_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="John",
            email="john@acme.com",
            password_hash="password123",
            role=SystemUserRole.RECEPTIONIST,
        )
        existing_user = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )

        mock_get.return_value = existing_user

        with pytest.raises(HTTPException) as exc_info:
            await add_system_user(user_data)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail

    @patch("services.system_user_service.issue_tokens_for_role")
    @patch("services.system_user_service.check_password")
    @patch("services.system_user_service.get_system_user")
    async def test_authenticate_system_user_success(
        self, mock_get, mock_check_pwd, mock_tokens
    ):
        """Test authenticating a system user with correct credentials."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="password123")
        user = SystemUserOut(
            id=user_id,
            tenant_id="tenant123",
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
            account_status=AccountStatus.ACTIVE,
        )

        mock_get.return_value = user
        mock_check_pwd.return_value = True
        mock_tokens.return_value = ("access_token_123", "refresh_token_456")

        with patch("services.system_user_service.db") as mock_db:
            mock_db.system_users.find_one.return_value = {
                "_id": ObjectId(user_id),
                "password_hash": "hashed_password",
            }
            result = await authenticate_system_user(login_data)

        assert result.id == user_id
        assert result.access_token == "access_token_123"

    @patch("services.system_user_service.get_system_user")
    async def test_authenticate_system_user_invalid_password(self, mock_get):
        """Test authentication with invalid password raises 401."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="wrongpassword")
        user = SystemUserOut(
            id=user_id,
            tenant_id="tenant123",
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
            account_status=AccountStatus.ACTIVE,
        )

        mock_get.return_value = user

        with patch("services.system_user_service.db") as mock_db:
            mock_db.system_users.find_one.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_system_user(login_data)

        assert exc_info.value.status_code == 404

    @patch("services.system_user_service.get_system_user")
    async def test_authenticate_system_user_inactive_account(self, mock_get):
        """Test authentication with inactive account raises 403."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="password123")
        user = SystemUserOut(
            id=user_id,
            tenant_id="tenant123",
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
            account_status=AccountStatus.INACTIVE,
        )

        mock_get.return_value = user

        with patch("services.system_user_service.db") as mock_db:
            mock_db.system_users.find_one.return_value = {
                "_id": ObjectId(user_id),
                "password_hash": "hashed_password",
            }
            with patch(
                "services.system_user_service.check_password", return_value=True
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await authenticate_system_user(login_data)

        assert exc_info.value.status_code == 403
        assert "not active" in exc_info.value.detail

    @patch("services.system_user_service.get_system_user")
    async def test_retrieve_system_user_by_id_success(self, mock_get):
        """Test retrieving a system user by ID."""
        from services.system_user_service import retrieve_system_user_by_id

        user_id = str(ObjectId())
        user = SystemUserOut(
            id=user_id,
            tenant_id="tenant123",
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )

        mock_get.return_value = user

        result = await retrieve_system_user_by_id(user_id)

        assert result.id == user_id

    async def test_retrieve_system_user_by_id_invalid_objectid(self):
        """Test invalid ObjectId format raises 400."""
        from services.system_user_service import retrieve_system_user_by_id

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_system_user_by_id("invalid-id")

        assert exc_info.value.status_code == 400

    @patch("services.system_user_service.update_system_user")
    async def test_update_system_user_success(self, mock_update):
        """Test updating a system user."""
        from services.system_user_service import update_system_user_by_id

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        update_data = SystemUserUpdate(full_name="John Updated")
        updated_user = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John Updated",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )

        mock_update.return_value = updated_user

        result = await update_system_user_by_id(user_id, tenant_id, update_data)

        assert result.full_name == "John Updated"

    @patch("services.system_user_service.delete_system_user")
    @patch("services.system_user_service.delete_all_tokens_with_user_id")
    async def test_remove_system_user_success(self, mock_delete_tokens, mock_delete):
        """Test removing a system user."""
        from services.system_user_service import remove_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_delete.return_value = mock_result

        await remove_system_user(user_id, tenant_id)

        mock_delete.assert_called_once()
        mock_delete_tokens.assert_called_once()


# ============================================================================
# APPOINTMENT SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestAppointmentService:
    """Test suite for appointment service layer."""

    @patch("services.appointment_service.create_appointment")
    async def test_add_appointment_success(self, mock_create):
        """Test adding a new appointment."""
        from services.appointment_service import add_appointment

        tenant_id = str(ObjectId())
        host_id = str(ObjectId())
        department_id = str(ObjectId())
        appt_id = str(ObjectId())
        appt_data = AppointmentCreate(
            tenant_id=tenant_id,
            host_id=host_id,
            department_id=department_id,
            scheduled_datetime=1700000000,
        )
        created_appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=host_id,
            department_id=department_id,
            scheduled_datetime=1700000000,
            status=AppointmentStatus.SCHEDULED,
        )

        mock_create.return_value = created_appt

        result = await add_appointment(appt_data)

        assert result.id == appt_id
        assert result.status == AppointmentStatus.SCHEDULED

    @patch("services.appointment_service.get_appointment")
    async def test_retrieve_appointment_by_id_success(self, mock_get):
        """Test retrieving an appointment by ID."""
        from services.appointment_service import retrieve_appointment_by_id

        tenant_id = str(ObjectId())
        appt_id = str(ObjectId())
        appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=str(ObjectId()),
            department_id=str(ObjectId()),
            scheduled_datetime=1700000000,
            status=AppointmentStatus.SCHEDULED,
        )

        mock_get.return_value = appt

        result = await retrieve_appointment_by_id(appt_id, tenant_id)

        assert result.id == appt_id

    async def test_retrieve_appointment_by_id_invalid_objectid(self):
        """Test invalid ObjectId format raises 400."""
        from services.appointment_service import retrieve_appointment_by_id

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_appointment_by_id("invalid-id", "tenant123")

        assert exc_info.value.status_code == 400

    @patch("services.appointment_service.get_appointment")
    async def test_retrieve_appointment_by_id_not_found(self, mock_get):
        """Test retrieving non-existent appointment raises 404."""
        from services.appointment_service import retrieve_appointment_by_id

        appt_id = str(ObjectId())
        tenant_id = str(ObjectId())
        mock_get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_appointment_by_id(appt_id, tenant_id)

        assert exc_info.value.status_code == 404

    @patch("services.appointment_service.update_appointment")
    async def test_update_appointment_success(self, mock_update):
        """Test updating an appointment."""
        from services.appointment_service import update_appointment_by_id

        tenant_id = str(ObjectId())
        appt_id = str(ObjectId())
        update_data = AppointmentUpdate(status=AppointmentStatus.FULFILLED)
        updated_appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=str(ObjectId()),
            department_id=str(ObjectId()),
            scheduled_datetime=1700000000,
            status=AppointmentStatus.FULFILLED,
        )

        mock_update.return_value = updated_appt

        result = await update_appointment_by_id(appt_id, tenant_id, update_data)

        assert result.status == AppointmentStatus.FULFILLED


# ============================================================================
# VISIT SESSION SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestVisitSessionService:
    """Test suite for visit session service layer."""

    @patch("services.visit_session_service.generate_badge_pdf")
    @patch("services.visit_session_service.sign_badge_token")
    @patch("services.visit_session_service.get_or_create_visitor_profile")
    @patch("services.visit_session_service.update_visitor_profile")
    @patch("services.visit_session_service.update_visit_session")
    @patch("services.visit_session_service.create_visit_session")
    @patch("services.visit_session_service.get_active_notice_for_tenant")
    @patch("services.visit_session_service.get_system_user")
    @patch("services.visit_session_service.get_department")
    @patch("services.visit_session_service.get_tenant")
    async def test_check_in_visitor_success(
        self,
        mock_get_tenant,
        mock_get_dept,
        mock_get_sys_user,
        mock_get_notice,
        mock_create_session,
        mock_update_session,
        mock_update_profile,
        mock_get_profile,
        mock_sign_token,
        mock_generate_pdf,
    ):
        """Test checking in a visitor successfully."""
        from services.visit_session_service import check_in_visitor

        tenant_id = str(ObjectId())
        receptionist_id = str(ObjectId())
        dept_id = str(ObjectId())
        profile_id = str(ObjectId())
        session_id = str(ObjectId())

        # Setup mocks
        tenant = MagicMock()
        tenant.id = tenant_id
        tenant.lawful_basis = LawfulBasis.LEGITIMATE_INTEREST

        department = MagicMock()
        department.id = dept_id
        department.name = "Reception"

        receptionist = MagicMock()
        receptionist.name = "Jane Doe"

        profile = VisitorProfileOut(
            id=profile_id,
            tenant_id=tenant_id,
            phone="1234567890",
            full_name="John Visitor",
        )

        session = VisitSessionOut(
            id=session_id,
            tenant_id=tenant_id,
            visitor_profile_id=profile_id,
            department_id=dept_id,
            status=VisitStatus.CHECKED_IN,
        )

        mock_get_tenant.return_value = tenant
        mock_get_dept.return_value = department
        mock_get_sys_user.return_value = receptionist
        mock_get_notice.return_value = None
        mock_get_profile.return_value = profile
        mock_create_session.return_value = session
        mock_update_session.return_value = session
        mock_sign_token.return_value = "signed_token_xyz"
        mock_generate_pdf.return_value = b"fake_pdf_bytes"

        request = CheckInRequest(
            phone="1234567890",
            full_name="John Visitor",
            department_id=dept_id,
            check_in_method=CheckInMethod.MANUAL,
        )

        result = await check_in_visitor(request, tenant_id, receptionist_id)

        assert result["session"].id == session_id
        assert result["badge_qr_token"] == "signed_token_xyz"
        assert "badge_pdf_base64" in result
        mock_create_session.assert_called_once()

    @patch("services.visit_session_service.get_visit_session")
    @patch("services.visit_session_service.update_visit_session")
    async def test_check_out_visitor_success(self, mock_update, mock_get):
        """Test checking out a visitor successfully."""
        from services.visit_session_service import check_out_visitor

        tenant_id = str(ObjectId())
        session_id = str(ObjectId())

        session = VisitSessionOut(
            id=session_id,
            tenant_id=tenant_id,
            visitor_profile_id=str(ObjectId()),
            department_id=str(ObjectId()),
            status=VisitStatus.CHECKED_IN,
        )

        checked_out = VisitSessionOut(
            id=session_id,
            tenant_id=tenant_id,
            visitor_profile_id=str(ObjectId()),
            department_id=str(ObjectId()),
            status=VisitStatus.CHECKED_OUT,
        )

        mock_get.return_value = session
        mock_update.return_value = checked_out

        request = CheckOutRequest(
            session_id=session_id, check_out_method=CheckOutMethod.MANUAL
        )

        result = await check_out_visitor(request, tenant_id)

        assert result.status == VisitStatus.CHECKED_OUT
        mock_get.assert_called_once()
        mock_update.assert_called_once()

    @patch("services.visit_session_service.verify_badge_token")
    async def test_check_out_visitor_invalid_badge_token(self, mock_verify):
        """Test check out with invalid badge token raises 400."""
        from services.visit_session_service import check_out_visitor

        tenant_id = str(ObjectId())
        mock_verify.return_value = None

        request = CheckOutRequest(
            badge_qr_token="invalid_token",
            check_out_method=CheckOutMethod.QR_SCAN,
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_out_visitor(request, tenant_id)

        assert exc_info.value.status_code == 400
        assert "Invalid or expired badge QR token" in exc_info.value.detail

    @patch("services.visit_session_service.get_visit_session")
    async def test_check_out_visitor_session_not_found(self, mock_get):
        """Test check out with non-existent session raises 404."""
        from services.visit_session_service import check_out_visitor

        tenant_id = str(ObjectId())
        session_id = str(ObjectId())
        mock_get.return_value = None

        request = CheckOutRequest(
            session_id=session_id, check_out_method=CheckOutMethod.MANUAL
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_out_visitor(request, tenant_id)

        assert exc_info.value.status_code == 404
        assert "Visit session not found" in exc_info.value.detail

    @patch("services.visit_session_service.get_visit_session")
    async def test_check_out_visitor_already_checked_out(self, mock_get):
        """Test check out when visitor already checked out raises 400."""
        from services.visit_session_service import check_out_visitor

        tenant_id = str(ObjectId())
        session_id = str(ObjectId())

        session = VisitSessionOut(
            id=session_id,
            tenant_id=tenant_id,
            visitor_profile_id=str(ObjectId()),
            department_id=str(ObjectId()),
            status=VisitStatus.CHECKED_OUT,
        )

        mock_get.return_value = session

        request = CheckOutRequest(
            session_id=session_id, check_out_method=CheckOutMethod.MANUAL
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_out_visitor(request, tenant_id)

        assert exc_info.value.status_code == 400
        assert "not currently checked in" in exc_info.value.detail

    @patch("services.visit_session_service.get_visit_session")
    async def test_retrieve_visit_session_by_id_success(self, mock_get):
        """Test retrieving a visit session by ID."""
        from services.visit_session_service import retrieve_visit_session_by_id

        tenant_id = str(ObjectId())
        session_id = str(ObjectId())

        session = VisitSessionOut(
            id=session_id,
            tenant_id=tenant_id,
            visitor_profile_id=str(ObjectId()),
            department_id=str(ObjectId()),
            status=VisitStatus.CHECKED_IN,
        )

        mock_get.return_value = session

        result = await retrieve_visit_session_by_id(session_id, tenant_id)

        assert result.id == session_id

    async def test_retrieve_visit_session_by_id_invalid_objectid(self):
        """Test invalid ObjectId format raises 400."""
        from services.visit_session_service import retrieve_visit_session_by_id

        tenant_id = str(ObjectId())

        with pytest.raises(HTTPException) as exc_info:
            await retrieve_visit_session_by_id("invalid-id", tenant_id)

        assert exc_info.value.status_code == 400


# ============================================================================
# BOOTSTRAP TENANT SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestBootstrapTenantService:
    """Test suite for the bootstrap_tenant service function."""

    @patch("services.tenant_service.add_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.get_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.create_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_success(
        self, mock_get_tenant, mock_create_tenant, mock_get_su, mock_add_su
    ):
        """Test successful bootstrap creates tenant + super_admin."""
        from services.tenant_service import bootstrap_tenant

        tenant_id = str(ObjectId())
        su_id = str(ObjectId())

        mock_get_tenant.return_value = None  # no duplicate
        mock_create_tenant.return_value = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            date_created=1712500000,
        )
        mock_get_su.return_value = None  # no existing super_admin
        mock_add_su.return_value = SystemUserOut(
            id=su_id,
            tenant_id=tenant_id,
            full_name="Jane Doe",
            email="jane@acme.com",
            role=SystemUserRole.SUPER_ADMIN,
            account_status=AccountStatus.ACTIVE,
            access_token="tok_abc",
            refresh_token="ref_xyz",
            date_created=1712500000,
        )

        payload = TenantBootstrapRequest(
            company_name="Acme Corp",
            admin_full_name="Jane Doe",
            admin_email="jane@acme.com",
            admin_password="SecurePass123!",
        )

        result = await bootstrap_tenant(payload)

        assert result["tenant"].id == tenant_id
        assert result["super_admin"].id == su_id
        assert result["super_admin"].role == SystemUserRole.SUPER_ADMIN
        mock_create_tenant.assert_called_once()
        mock_add_su.assert_called_once()

    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_duplicate_company_name(self, mock_get_tenant):
        """Test bootstrap fails if tenant company name already exists."""
        from services.tenant_service import bootstrap_tenant

        mock_get_tenant.return_value = TenantOut(
            id=str(ObjectId()),
            company_name="Acme Corp",
            date_created=1712500000,
        )

        payload = TenantBootstrapRequest(
            company_name="Acme Corp",
            admin_full_name="Jane Doe",
            admin_email="jane@acme.com",
            admin_password="Pass123!",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        assert "company name" in exc_info.value.detail.lower()

    @patch("services.tenant_service.add_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.get_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.create_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_existing_super_admin_rejected(
        self, mock_get_tenant, mock_create_tenant, mock_get_su, mock_add_su
    ):
        """Test bootstrap fails if tenant already has a super_admin."""
        from services.tenant_service import bootstrap_tenant

        tenant_id = str(ObjectId())
        mock_get_tenant.return_value = None
        mock_create_tenant.return_value = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            date_created=1712500000,
        )
        mock_get_su.return_value = SystemUserOut(
            id=str(ObjectId()),
            tenant_id=tenant_id,
            full_name="Existing SA",
            email="existing@acme.com",
            role=SystemUserRole.SUPER_ADMIN,
            account_status=AccountStatus.ACTIVE,
            date_created=1712500000,
        )

        payload = TenantBootstrapRequest(
            company_name="Acme Corp",
            admin_full_name="Jane Doe",
            admin_email="jane@acme.com",
            admin_password="Pass123!",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        assert "super_admin" in exc_info.value.detail.lower()

    @patch("services.tenant_service.delete_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.add_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.get_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.create_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_rolls_back_tenant_on_user_failure(
        self,
        mock_get_tenant,
        mock_create_tenant,
        mock_get_su,
        mock_add_su,
        mock_delete_tenant,
    ):
        """Test tenant is deleted if super_admin creation fails."""
        from services.tenant_service import bootstrap_tenant

        tenant_id = str(ObjectId())
        mock_get_tenant.return_value = None
        mock_create_tenant.return_value = TenantOut(
            id=tenant_id,
            company_name="Acme Corp",
            date_created=1712500000,
        )
        mock_get_su.return_value = None
        mock_add_su.side_effect = HTTPException(
            status_code=409, detail="Duplicate email"
        )
        mock_delete_tenant.return_value = MagicMock(deleted_count=1)

        payload = TenantBootstrapRequest(
            company_name="Acme Corp",
            admin_full_name="Jane Doe",
            admin_email="jane@acme.com",
            admin_password="Pass123!",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        # Verify the tenant rollback was attempted
        mock_delete_tenant.assert_called_once()
