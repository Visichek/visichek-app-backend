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
        mock_create.assert_called_once_with(tenant_data, preassigned_id=None)

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

    @patch(
        "services.system_user_service.resolve_hq_branch_id",
        new_callable=AsyncMock,
        return_value="hq-branch",
    )
    @patch(
        "services.system_user_service._resolve_branch_ids_for_user_assignment",
        new_callable=AsyncMock,
        return_value=["branch-1"],
    )
    @patch("services.system_user_service.enforce_entity_cap", new_callable=AsyncMock)
    @patch(
        "services.system_user_service.get_system_users",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "services.system_user_service.count_system_users",
        new_callable=AsyncMock,
        return_value=0,
    )
    @patch("services.system_user_service.issue_tokens_for_role")
    @patch("services.system_user_service.create_system_user")
    @patch("services.system_user_service.get_system_user")
    async def test_add_system_user_success(
        self,
        mock_get,
        mock_create,
        mock_tokens,
        mock_count,
        mock_get_many,
        mock_cap,
        mock_resolve_branch,
        mock_resolve_hq,
    ):
        """Test adding a new system user."""
        from services.system_user_service import add_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        user_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="John Receptionist",
            email="john@acme.com",
            password_hash="MyStr0ng!Passw0rd#2026",
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

    @patch(
        "services.system_user_service.resolve_hq_branch_id",
        new_callable=AsyncMock,
        return_value="hq-branch",
    )
    @patch("services.system_user_service.enforce_entity_cap", new_callable=AsyncMock)
    @patch(
        "services.system_user_service.count_system_users",
        new_callable=AsyncMock,
        return_value=0,
    )
    @patch("services.system_user_service.get_system_users", new_callable=AsyncMock)
    @patch("services.system_user_service.get_system_user")
    async def test_add_system_user_duplicate_email_raises_409(
        self, mock_get, mock_get_many, mock_count, mock_cap, mock_resolve_hq
    ):
        """Test adding user with duplicate email raises 409."""
        from services.system_user_service import add_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        user_data = SystemUserCreate(
            tenant_id=tenant_id,
            full_name="John",
            email="john@acme.com",
            password_hash="MyStr0ng!Passw0rd#2026",
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
        mock_get_many.return_value = [existing_user]

        with pytest.raises(HTTPException) as exc_info:
            await add_system_user(user_data)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail

    @patch(
        "security.password_policy.check_login_lockout",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "security.password_policy.record_failed_login",
        new_callable=AsyncMock,
        return_value={"locked": False, "attempts_remaining": 4},
    )
    @patch("security.password_policy.clear_failed_logins", new_callable=AsyncMock)
    @patch("services.system_user_service.issue_tokens_for_role")
    @patch("services.system_user_service.check_password")
    @patch("services.system_user_service.get_raw_system_users_by_email")
    async def test_authenticate_system_user_success(
        self,
        mock_get_raw,
        mock_check_pwd,
        mock_tokens,
        mock_clear,
        mock_record,
        mock_lockout,
    ):
        """Single-tenant match: returns SystemUserOut with tokens."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="password123")

        mock_get_raw.return_value = [
            {
                "_id": ObjectId(user_id),
                "tenant_id": "tenant123",
                "full_name": "John",
                "email": "john@acme.com",
                "role": SystemUserRole.RECEPTIONIST.value,
                "account_status": AccountStatus.ACTIVE.value,
                "password_hash": "hashed_password",
            }
        ]
        mock_check_pwd.return_value = True
        mock_tokens.return_value = ("access_token_123", "refresh_token_456")

        with patch(
            "services.otp_service.is_mfa_required",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await authenticate_system_user(login_data)

        assert result.id == user_id
        assert result.access_token == "access_token_123"

    @patch(
        "security.password_policy.check_login_lockout",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "security.password_policy.record_failed_login",
        new_callable=AsyncMock,
        return_value={"locked": False, "attempts_remaining": 4},
    )
    @patch("services.system_user_service.check_password")
    @patch("services.system_user_service.get_raw_system_users_by_email")
    async def test_authenticate_system_user_invalid_password(
        self, mock_get_raw, mock_check_pwd, mock_record, mock_lockout
    ):
        """Wrong password against the only matching record -> 401."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="wrongpassword")
        mock_get_raw.return_value = [
            {
                "_id": ObjectId(user_id),
                "tenant_id": "tenant123",
                "full_name": "John",
                "email": "john@acme.com",
                "role": SystemUserRole.RECEPTIONIST.value,
                "account_status": AccountStatus.ACTIVE.value,
                "password_hash": "hashed_password",
            }
        ]
        mock_check_pwd.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_system_user(login_data)

        assert exc_info.value.status_code in (401, 429)

    @patch(
        "security.password_policy.check_login_lockout",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "security.password_policy.record_failed_login",
        new_callable=AsyncMock,
        return_value={"locked": False, "attempts_remaining": 4},
    )
    @patch("security.password_policy.clear_failed_logins", new_callable=AsyncMock)
    @patch("services.system_user_service.check_password")
    @patch("services.system_user_service.get_raw_system_users_by_email")
    async def test_authenticate_system_user_inactive_account(
        self,
        mock_get_raw,
        mock_check_pwd,
        mock_clear,
        mock_record,
        mock_lockout,
    ):
        """Password matches but account not ACTIVE -> 403."""
        from services.system_user_service import authenticate_system_user

        user_id = str(ObjectId())
        login_data = SystemUserLogin(email="john@acme.com", password="password123")
        mock_get_raw.return_value = [
            {
                "_id": ObjectId(user_id),
                "tenant_id": "tenant123",
                "full_name": "John",
                "email": "john@acme.com",
                "role": SystemUserRole.RECEPTIONIST.value,
                "account_status": AccountStatus.INACTIVE.value,
                "password_hash": "hashed_password",
            }
        ]
        mock_check_pwd.return_value = True

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_system_user(login_data)

        assert exc_info.value.status_code == 403
        assert "not active" in exc_info.value.detail

    @patch(
        "security.password_policy.check_login_lockout",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("security.password_policy.clear_failed_logins", new_callable=AsyncMock)
    @patch(
        "services.tenant_selection_service.create_tenant_selection_challenge",
        new_callable=AsyncMock,
        return_value="sel-token-abc",
    )
    @patch("services.system_user_service.check_password")
    @patch("services.system_user_service.get_raw_system_users_by_email")
    async def test_authenticate_system_user_multi_tenant_returns_selection(
        self,
        mock_get_raw,
        mock_check_pwd,
        mock_create_sel,
        mock_clear,
        mock_lockout,
    ):
        """Two ACTIVE matches -> returns tenant selection challenge."""
        from services.system_user_service import authenticate_system_user

        login_data = SystemUserLogin(email="john@acme.com", password="password123")
        uid_a, uid_b = str(ObjectId()), str(ObjectId())
        mock_get_raw.return_value = [
            {
                "_id": ObjectId(uid_a),
                "tenant_id": "tenant-a",
                "full_name": "John A",
                "email": "john@acme.com",
                "role": SystemUserRole.RECEPTIONIST.value,
                "account_status": AccountStatus.ACTIVE.value,
                "password_hash": "h1",
            },
            {
                "_id": ObjectId(uid_b),
                "tenant_id": "tenant-b",
                "full_name": "John B",
                "email": "john@acme.com",
                "role": SystemUserRole.DEPT_ADMIN.value,
                "account_status": AccountStatus.ACTIVE.value,
                "password_hash": "h2",
            },
        ]
        mock_check_pwd.return_value = True

        with patch(
            "services.system_user_service._build_tenant_options",
            new_callable=AsyncMock,
            return_value=[
                {
                    "tenant_id": "tenant-a",
                    "company_name": "A Co",
                    "role": "receptionist",
                    "full_name": "John A",
                    "mfa_enabled": False,
                },
                {
                    "tenant_id": "tenant-b",
                    "company_name": "B Co",
                    "role": "dept_admin",
                    "full_name": "John B",
                    "mfa_enabled": True,
                },
            ],
        ):
            result = await authenticate_system_user(login_data)

        assert isinstance(result, dict)
        assert result["tenant_selection_required"] is True
        assert result["selection_token"] == "sel-token-abc"
        assert {t["tenant_id"] for t in result["tenants"]} == {"tenant-a", "tenant-b"}
        mock_create_sel.assert_awaited_once()

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

    @patch("services.system_user_service.record_audit_event", new_callable=AsyncMock)
    @patch("services.system_user_service.get_system_user", new_callable=AsyncMock)
    @patch("services.system_user_service.update_system_user")
    async def test_update_system_user_success(self, mock_update, mock_get, mock_audit):
        """Test updating a system user."""
        from services.system_user_service import update_system_user_by_id

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        update_data = SystemUserUpdate(full_name="John Updated")
        existing_user = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John Original",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )
        updated_user = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John Updated",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )

        mock_get.return_value = existing_user
        mock_update.return_value = updated_user

        result = await update_system_user_by_id(user_id, tenant_id, update_data)

        assert result.full_name == "John Updated"

    @patch("services.system_user_service.record_audit_event", new_callable=AsyncMock)
    @patch("services.system_user_service.get_system_user", new_callable=AsyncMock)
    @patch("services.system_user_service.delete_system_user")
    @patch("services.system_user_service.delete_all_tokens_with_user_id")
    async def test_remove_system_user_success(
        self, mock_delete_tokens, mock_delete, mock_get, mock_audit
    ):
        """Test removing a system user."""
        from services.system_user_service import remove_system_user

        tenant_id = str(ObjectId())
        user_id = str(ObjectId())
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_delete.return_value = mock_result
        mock_get.return_value = SystemUserOut(
            id=user_id,
            tenant_id=tenant_id,
            full_name="John",
            email="john@acme.com",
            role=SystemUserRole.RECEPTIONIST,
        )

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

    @patch(
        "services.host_service.resolve_host_identity",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "services.appointment_service.enforce_feature_enabled",
        new_callable=AsyncMock,
    )
    @patch(
        "services.appointment_service._validate_tenant_form_data_for_appointment",
        new_callable=AsyncMock,
    )
    @patch("services.appointment_service.enforce_entity_cap", new_callable=AsyncMock)
    @patch(
        "services.appointment_service.count_appointments",
        new_callable=AsyncMock,
        return_value=0,
    )
    @patch("services.appointment_service.create_appointment")
    async def test_add_appointment_success(
        self,
        mock_create,
        mock_count,
        mock_cap,
        mock_validate_form,
        mock_feature,
        mock_resolve_host,
    ):
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
            visitor_name_snapshot="Jane Smith",
            visitor_phone="+2348012345678",
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

    @patch("services.appointment_service.get_appointment")
    @patch("services.appointment_service.update_appointment")
    async def test_update_appointment_success(self, mock_update, mock_get):
        """Test updating an appointment."""
        import time as _time

        from services.appointment_service import update_appointment_by_id

        tenant_id = str(ObjectId())
        appt_id = str(ObjectId())
        future_ts = int(_time.time()) + 3600
        update_data = AppointmentUpdate(status=AppointmentStatus.FULFILLED)
        existing_appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=str(ObjectId()),
            department_id=str(ObjectId()),
            scheduled_datetime=future_ts,
            status=AppointmentStatus.SCHEDULED,
        )
        updated_appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=existing_appt.host_id,
            department_id=existing_appt.department_id,
            scheduled_datetime=future_ts,
            status=AppointmentStatus.FULFILLED,
        )

        mock_get.return_value = existing_appt
        mock_update.return_value = updated_appt

        result = await update_appointment_by_id(appt_id, tenant_id, update_data)

        assert result.status == AppointmentStatus.FULFILLED

    @patch("services.appointment_service.get_appointment")
    async def test_update_appointment_rejects_past_scheduled_date(self, mock_get):
        """Updates to appointments whose scheduled datetime has passed are rejected."""
        import time as _time

        from services.appointment_service import update_appointment_by_id

        tenant_id = str(ObjectId())
        appt_id = str(ObjectId())
        past_ts = int(_time.time()) - 3600
        existing_appt = AppointmentOut(
            id=appt_id,
            tenant_id=tenant_id,
            host_id=str(ObjectId()),
            department_id=str(ObjectId()),
            scheduled_datetime=past_ts,
            status=AppointmentStatus.SCHEDULED,
        )
        mock_get.return_value = existing_appt

        with pytest.raises(HTTPException) as exc_info:
            await update_appointment_by_id(
                appt_id,
                tenant_id,
                AppointmentUpdate(status=AppointmentStatus.FULFILLED),
            )
        assert exc_info.value.status_code == 400


# ============================================================================
# VISIT SESSION SERVICE TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestVisitSessionService:
    """Test suite for visit session service layer."""

    @patch(
        "services.visit_session_service.increment_visitor_profile_visits",
        new_callable=AsyncMock,
    )
    @patch(
        "services.visit_session_service.enforce_branch_visitor_cap",
        new_callable=AsyncMock,
    )
    @patch(
        "services.visit_session_service.get_plan_data_safe",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "services.visit_session_service.has_first_seen",
        new_callable=AsyncMock,
        return_value=False,
    )
    @patch(
        "services.visit_session_service.record_first_seen",
        new_callable=AsyncMock,
        return_value=True,
    )
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
        mock_record_first_seen,
        mock_has_first_seen,
        mockget_plan_data_safe,
        mock_cap_vs,
        mock_inc_visits,
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
        receptionist.full_name = "Jane Doe"

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

        request = CheckInRequest(
            phone="1234567890",
            full_name="John Visitor",
            department_id=dept_id,
            check_in_method=CheckInMethod.MANUAL,
        )

        # Pass an explicit branch_id (as the route does, resolved from the
        # receptionist's token) so the service skips the HQ-branch DB fallback.
        result = await check_in_visitor(
            request, tenant_id, receptionist_id, branch_id=str(ObjectId())
        )

        assert result["session"].id == session_id
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

        assert result.source_type == "visit_session"
        assert result.status == VisitStatus.CHECKED_OUT.value
        assert result.visit_session is checked_out
        mock_get.assert_called_once()
        mock_update.assert_called_once()

    @patch("services.visit_session_service.get_checkin", new_callable=AsyncMock)
    @patch("services.visit_session_service.update_checkin", new_callable=AsyncMock)
    async def test_check_out_approved_checkin_success(
        self, mock_update_checkin, mock_get_checkin
    ):
        """Approved check-ins can be checked out without a visit session."""
        from schemas.checkin_schema import CheckinOut, CheckinPurpose
        from schemas.imports import CheckinState
        from services.visit_session_service import check_out_visitor

        tenant_id = str(ObjectId())
        checkin_id = str(ObjectId())
        checkin = CheckinOut(
            id=checkin_id,
            tenant_id=tenant_id,
            visitor_id=str(ObjectId()),
            checkin_config_id="",
            tenant_specific_data={},
            purpose=CheckinPurpose(purpose="Maintenance"),
            state=CheckinState.APPROVED,
        )
        checked_out = checkin.model_copy(update={"state": CheckinState.CHECKED_OUT})
        mock_get_checkin.return_value = checkin
        mock_update_checkin.return_value = checked_out

        result = await check_out_visitor(
            CheckOutRequest(source_type="approved_checkin", checkout_id=checkin_id),
            tenant_id,
        )

        assert result.source_type == "approved_checkin"
        assert result.status == "checked_out"
        assert result.checkin is checked_out
        mock_get_checkin.assert_awaited_once()
        mock_update_checkin.assert_awaited_once()

    @patch(
        "services.visit_session_service.get_badge_by_qr_value",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("services.visit_session_service.verify_badge_token")
    async def test_check_out_visitor_invalid_badge_token(
        self, mock_verify, mock_get_badge
    ):
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

    async def test_awaiting_checkout_includes_approved_and_due_scheduled(self):
        """Awaiting checkout includes approved check-ins and due appointments."""
        from schemas.checkin_schema import CheckinOut, CheckinPurpose
        from schemas.imports import CheckinState
        from schemas.visitor_schema import VisitorOut
        from services.visit_session_service import retrieve_visitors_awaiting_checkout

        tenant_id = str(ObjectId())
        checkin_id = str(ObjectId())
        appointment_id = str(ObjectId())
        visitor_id = str(ObjectId())
        checkin = CheckinOut(
            id=checkin_id,
            tenant_id=tenant_id,
            visitor_id=visitor_id,
            checkin_config_id="",
            tenant_specific_data={},
            purpose=CheckinPurpose(purpose="Maintenance"),
            state=CheckinState.APPROVED,
            approved_at=100,
        )
        visitor = VisitorOut(
            id=visitor_id,
            tenant_id=tenant_id,
            full_name="James Bond",
            email="james@example.com",
            phone="+2348052964826",
            bio_data={"company": "Spy"},
            verified=True,
        )
        appointment = AppointmentOut(
            id=appointment_id,
            tenant_id=tenant_id,
            visitor_profile_id=str(ObjectId()),
            host_id=str(ObjectId()),
            department_id=str(ObjectId()),
            visitor_name_snapshot="Jane Scheduled",
            scheduled_datetime=200,
            purpose="Scheduled visit",
            status=AppointmentStatus.SCHEDULED,
        )

        with (
            patch(
                "services.visit_session_service.get_awaiting_checkout_sessions",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "services.visit_session_service.get_approved_checkins_for_checkout",
                new_callable=AsyncMock,
                return_value=[checkin],
            ),
            patch(
                "services.visit_session_service.get_due_scheduled_appointments_for_checkout",
                new_callable=AsyncMock,
                return_value=[appointment],
            ),
            patch(
                "services.visit_session_service.count_awaiting_checkout_sessions",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "services.visit_session_service.count_approved_checkins_for_checkout",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "services.visit_session_service.count_due_scheduled_appointments_for_checkout",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "repositories.visitor_repo.get_visitors_by_ids",
                new_callable=AsyncMock,
                return_value=[visitor],
            ),
            patch(
                "services.visit_session_service.get_badges_by_checkin_ids",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "services.summary_resolver.resolve_tenant_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.summary_resolver.resolve_department_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.summary_resolver.resolve_system_user_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.summary_resolver.resolve_visitor_profile_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "services.summary_resolver.resolve_appointment_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            items, total = await retrieve_visitors_awaiting_checkout(
                tenant_id=tenant_id,
                start=0,
                stop=50,
            )

        assert total == 2
        assert {item.source_type for item in items} == {
            "approved_checkin",
            "scheduled_appointment",
        }
        assert any(item.visitor_name == "James Bond" for item in items)
        assert any(item.visitor_name == "Jane Scheduled" for item in items)

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

    @patch(
        "services.branch_service.ensure_default_branch",
        new_callable=AsyncMock,
        return_value=MagicMock(id="branch-1"),
    )
    @patch("services.system_user_service.add_system_user", new_callable=AsyncMock)
    @patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.create_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_success(
        self,
        mock_get_tenant,
        mock_create_tenant,
        mock_get_su,
        mock_add_su,
        mock_ensure_branch,
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
            admin_password="MyStr0ng!Passw0rd#2026",
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
            admin_password="MyStr0ng!Passw0rd#2026",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        assert "company name" in exc_info.value.detail.lower()

    @patch("services.system_user_service.add_system_user", new_callable=AsyncMock)
    @patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
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
            admin_password="MyStr0ng!Passw0rd#2026",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        assert "super_admin" in exc_info.value.detail.lower()

    @patch(
        "services.branch_service.ensure_default_branch",
        new_callable=AsyncMock,
        return_value=MagicMock(id="branch-1"),
    )
    @patch("services.tenant_service.delete_tenant", new_callable=AsyncMock)
    @patch("services.system_user_service.add_system_user", new_callable=AsyncMock)
    @patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
    @patch("services.tenant_service.create_tenant", new_callable=AsyncMock)
    @patch("services.tenant_service.get_tenant", new_callable=AsyncMock)
    async def test_bootstrap_rolls_back_tenant_on_user_failure(
        self,
        mock_get_tenant,
        mock_create_tenant,
        mock_get_su,
        mock_add_su,
        mock_delete_tenant,
        mock_ensure_branch,
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
            admin_password="MyStr0ng!Passw0rd#2026",
        )

        with pytest.raises(HTTPException) as exc_info:
            await bootstrap_tenant(payload)

        assert exc_info.value.status_code == 409
        # Verify the tenant rollback was attempted
        mock_delete_tenant.assert_called_once()


# ============================================================================
# AUDIT LOG ENRICHMENT TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestAuditLogEnrichment:
    """Batch enrichment of audit rows with actor / tenant / resource summaries."""

    async def test_enrich_audit_logs_attaches_summaries_and_batches(self):
        from schemas.audit_log_schema import AuditLogOut
        from schemas.summary_schema import TenantBriefSummary, UserBriefSummary
        from services.audit_service import enrich_audit_logs

        tenant_id = str(ObjectId())
        actor_id = str(ObjectId())
        # Two rows by the SAME actor in the SAME tenant — the batch resolvers
        # should be called once each regardless of row count.
        logs = [
            AuditLogOut(
                _id=str(ObjectId()),
                actor_id=actor_id,
                actor_role="super_admin",
                action="user_location.update",
                resource_type="user_location",
                resource_id=str(ObjectId()),
                tenant_id=tenant_id,
                timestamp=1779240786,
            ),
            AuditLogOut(
                _id=str(ObjectId()),
                actor_id=actor_id,
                actor_role="super_admin",
                action="appointment.created",
                resource_type="appointment",
                resource_id=str(ObjectId()),
                tenant_id=tenant_id,
                timestamp=1779240900,
            ),
        ]

        actor_summary = UserBriefSummary(
            id=actor_id,
            full_name="Jane Doe",
            email="jane@acme.com",
            role="super_admin",
            user_type="system_user",
        )
        tenant_summary = TenantBriefSummary(
            id=tenant_id, company_name="Acme Corp", is_active=True
        )

        with (
            patch(
                "services.summary_resolver.resolve_user_summaries_batch",
                new_callable=AsyncMock,
                return_value={actor_id: actor_summary},
            ) as mock_actors,
            patch(
                "services.summary_resolver.resolve_tenant_summaries_batch",
                new_callable=AsyncMock,
                return_value={tenant_id: tenant_summary},
            ) as mock_tenants,
            patch(
                "services.audit_service._resolve_resource_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            enriched = await enrich_audit_logs(logs)

        # One batch query per collection, not one per row.
        mock_actors.assert_awaited_once()
        mock_tenants.assert_awaited_once()
        assert len(enriched) == 2
        assert enriched[0].actor_summary is not None
        assert enriched[0].actor_summary.full_name == "Jane Doe"
        assert enriched[0].actor_summary.email == "jane@acme.com"
        assert enriched[0].tenant_summary is not None
        assert enriched[0].tenant_summary.company_name == "Acme Corp"

    async def test_enrich_audit_logs_deleted_actor_fallback(self):
        from schemas.audit_log_schema import AuditLogOut
        from services.audit_service import enrich_audit_logs

        actor_id = str(ObjectId())
        logs = [
            AuditLogOut(
                _id=str(ObjectId()),
                actor_id=actor_id,
                actor_role="dpo",
                action="incident.deleted",
                resource_type="incident",
                resource_id=str(ObjectId()),
                tenant_id=str(ObjectId()),
                timestamp=1779240786,
            )
        ]

        with (
            patch(
                "services.summary_resolver.resolve_user_summaries_batch",
                new_callable=AsyncMock,
                return_value={},  # actor not found — deleted
            ),
            patch(
                "services.summary_resolver.resolve_tenant_summaries_batch",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "services.audit_service._resolve_resource_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            enriched = await enrich_audit_logs(logs)

        assert len(enriched) == 1
        summary = enriched[0].actor_summary
        assert summary is not None
        assert summary.id == actor_id
        assert summary.role == "dpo"
        assert summary.user_type == "deleted"
        assert summary.full_name is None

    async def test_enrich_audit_logs_empty(self):
        from services.audit_service import enrich_audit_logs

        assert await enrich_audit_logs([]) == []
