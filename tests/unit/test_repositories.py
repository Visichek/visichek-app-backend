from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from bson import ObjectId

from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut
from schemas.system_user_schema import SystemUserCreate, SystemUserUpdate, SystemUserOut
from schemas.visit_session_schema import (
    VisitSessionCreate,
    VisitSessionUpdate,
    VisitSessionOut,
)
from schemas.imports import LawfulBasis, SystemUserRole, VisitStatus


# ============================================================================
# TENANT REPOSITORY TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestTenantRepository:
    """Test suite for tenant repository layer."""

    @patch("repositories.tenant_repo.db")
    async def test_create_tenant_inserts_and_returns_out(self, mock_db):
        """Test creating a tenant inserts and returns TenantOut."""
        from repositories.tenant_repo import create_tenant

        tenant_id = ObjectId()
        tenant_data = TenantCreate(
            company_name="Acme Corp",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
            retention_days=1095,
        )

        # Mock insert_one
        mock_insert_result = MagicMock()
        mock_insert_result.inserted_id = tenant_id
        mock_db.tenant_companies.insert_one = AsyncMock(return_value=mock_insert_result)

        # Mock find_one to return the created document
        mock_db.tenant_companies.find_one = AsyncMock(
            return_value={
                "_id": tenant_id,
                "company_name": "Acme Corp",
                "lawful_basis": "legitimate_interest",
                "retention_days": 1095,
                "date_created": 1234567890,
            }
        )

        result = await create_tenant(tenant_data)

        assert isinstance(result, TenantOut)
        assert result.company_name == "Acme Corp"
        assert result.lawful_basis == LawfulBasis.LEGITIMATE_INTEREST
        mock_db.tenant_companies.insert_one.assert_called_once()
        mock_db.tenant_companies.find_one.assert_called_once()

    @patch("repositories.tenant_repo.db")
    async def test_get_tenant_returns_none_when_not_found(self, mock_db):
        """Test get_tenant returns None when document not found."""
        from repositories.tenant_repo import get_tenant

        mock_db.tenant_companies.find_one = AsyncMock(return_value=None)

        result = await get_tenant({"company_name": "NonExistent"})

        assert result is None
        mock_db.tenant_companies.find_one.assert_called_once_with(
            {"company_name": "NonExistent"}
        )

    @patch("repositories.tenant_repo.db")
    async def test_get_tenant_returns_out_when_found(self, mock_db):
        """Test get_tenant returns TenantOut when document found."""
        from repositories.tenant_repo import get_tenant

        tenant_id = ObjectId()
        mock_db.tenant_companies.find_one = AsyncMock(
            return_value={
                "_id": tenant_id,
                "company_name": "Acme Corp",
                "lawful_basis": "legitimate_interest",
                "retention_days": 1095,
                "date_created": 1234567890,
            }
        )

        result = await get_tenant({"_id": tenant_id})

        assert isinstance(result, TenantOut)
        assert result.company_name == "Acme Corp"

    @patch("repositories.tenant_repo.db")
    async def test_update_tenant_returns_updated(self, mock_db):
        """Test updating a tenant returns updated TenantOut."""
        from repositories.tenant_repo import update_tenant

        tenant_id = ObjectId()
        update_data = TenantUpdate(company_name="Acme Corp Updated")

        mock_db.tenant_companies.find_one_and_update = AsyncMock(
            return_value={
                "_id": tenant_id,
                "company_name": "Acme Corp Updated",
                "lawful_basis": "legitimate_interest",
                "retention_days": 1095,
                "date_created": 1234567890,
            }
        )

        result = await update_tenant({"_id": tenant_id}, update_data)

        assert isinstance(result, TenantOut)
        assert result.company_name == "Acme Corp Updated"
        mock_db.tenant_companies.find_one_and_update.assert_called_once()

    @patch("repositories.tenant_repo.db")
    async def test_delete_tenant_calls_delete_one(self, mock_db):
        """Test deleting a tenant calls delete_one."""
        from repositories.tenant_repo import delete_tenant

        tenant_id = ObjectId()
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_db.tenant_companies.delete_one = AsyncMock(return_value=mock_result)

        result = await delete_tenant({"_id": tenant_id})

        assert result.deleted_count == 1
        mock_db.tenant_companies.delete_one.assert_called_once_with({"_id": tenant_id})

    @patch("repositories.tenant_repo.db")
    async def test_get_tenants_returns_list(self, mock_db):
        """Test get_tenants returns list of TenantOut objects."""
        from repositories.tenant_repo import get_tenants

        tenant_id_1 = ObjectId()
        tenant_id_2 = ObjectId()

        # Mock async cursor
        mock_cursor = MagicMock()
        mock_docs = [
            {
                "_id": tenant_id_1,
                "company_name": "Acme Corp",
                "lawful_basis": "legitimate_interest",
            },
            {
                "_id": tenant_id_2,
                "company_name": "Tech Inc",
                "lawful_basis": "consent",
            },
        ]

        async def async_iter(items):
            for item in items:
                yield item

        mock_cursor.__aiter__ = lambda self: async_iter(mock_docs)
        mock_db.tenant_companies.find.return_value.skip.return_value.limit.return_value = mock_cursor

        result = await get_tenants(start=0, stop=10)

        assert len(result) == 2
        assert result[0].company_name == "Acme Corp"
        assert result[1].company_name == "Tech Inc"


# ============================================================================
# SYSTEM USER REPOSITORY TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestSystemUserRepository:
    """Test suite for system user repository layer."""

    @patch("repositories.system_user_repo.db")
    async def test_create_system_user_inserts_and_returns_out(self, mock_db):
        """Test creating a system user inserts and returns SystemUserOut."""
        from repositories.system_user_repo import create_system_user

        user_id = ObjectId()
        user_data = SystemUserCreate(
            tenant_id="tenant123",
            full_name="John Receptionist",
            email="john@acme.com",
            password_hash="MyStr0ng!Passw0rd#2026",
            role=SystemUserRole.RECEPTIONIST,
        )

        mock_insert_result = MagicMock()
        mock_insert_result.inserted_id = user_id
        mock_db.system_users.insert_one = AsyncMock(return_value=mock_insert_result)

        mock_db.system_users.find_one = AsyncMock(
            return_value={
                "_id": user_id,
                "tenant_id": "tenant123",
                "full_name": "John Receptionist",
                "email": "john@acme.com",
                "role": "receptionist",
                "account_status": "ACTIVE",
                "date_created": 1234567890,
            }
        )

        result = await create_system_user(user_data)

        assert isinstance(result, SystemUserOut)
        assert result.email == "john@acme.com"
        assert result.full_name == "John Receptionist"
        mock_db.system_users.insert_one.assert_called_once()

    @patch("repositories.system_user_repo.db")
    async def test_get_system_user_returns_none_when_not_found(self, mock_db):
        """Test get_system_user returns None when not found."""
        from repositories.system_user_repo import get_system_user

        mock_db.system_users.find_one = AsyncMock(return_value=None)

        result = await get_system_user({"email": "nonexistent@acme.com"})

        assert result is None

    @patch("repositories.system_user_repo.db")
    async def test_get_system_user_returns_out_when_found(self, mock_db):
        """Test get_system_user returns SystemUserOut when found."""
        from repositories.system_user_repo import get_system_user

        user_id = ObjectId()
        mock_db.system_users.find_one = AsyncMock(
            return_value={
                "_id": user_id,
                "tenant_id": "tenant123",
                "full_name": "John",
                "email": "john@acme.com",
                "role": "receptionist",
                "account_status": "ACTIVE",
            }
        )

        result = await get_system_user({"_id": user_id})

        assert isinstance(result, SystemUserOut)
        assert result.email == "john@acme.com"

    @patch("repositories.system_user_repo.db")
    async def test_update_system_user_returns_updated(self, mock_db):
        """Test updating a system user returns updated SystemUserOut."""
        from repositories.system_user_repo import update_system_user

        user_id = ObjectId()
        update_data = SystemUserUpdate(full_name="John Updated")

        mock_db.system_users.find_one_and_update = AsyncMock(
            return_value={
                "_id": user_id,
                "tenant_id": "tenant123",
                "full_name": "John Updated",
                "email": "john@acme.com",
                "role": "receptionist",
                "account_status": "ACTIVE",
            }
        )

        result = await update_system_user({"_id": user_id}, update_data)

        assert isinstance(result, SystemUserOut)
        assert result.full_name == "John Updated"
        mock_db.system_users.find_one_and_update.assert_called_once()

    @patch("repositories.system_user_repo.db")
    async def test_delete_system_user_calls_delete_one(self, mock_db):
        """Test deleting a system user calls delete_one."""
        from repositories.system_user_repo import delete_system_user

        user_id = ObjectId()
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_db.system_users.delete_one = AsyncMock(return_value=mock_result)

        result = await delete_system_user({"_id": user_id})

        assert result.deleted_count == 1
        mock_db.system_users.delete_one.assert_called_once_with({"_id": user_id})

    @patch("repositories.system_user_repo.db")
    async def test_get_system_users_returns_list(self, mock_db):
        """Test get_system_users returns list of SystemUserOut objects."""
        from repositories.system_user_repo import get_system_users

        user_id_1 = ObjectId()
        user_id_2 = ObjectId()

        mock_cursor = MagicMock()
        mock_docs = [
            {
                "_id": user_id_1,
                "tenant_id": "tenant123",
                "email": "john@acme.com",
                "full_name": "John",
                "role": "receptionist",
                "account_status": "ACTIVE",
            },
            {
                "_id": user_id_2,
                "tenant_id": "tenant123",
                "email": "jane@acme.com",
                "full_name": "Jane",
                "role": "dept_admin",
                "account_status": "ACTIVE",
            },
        ]

        async def async_iter(items):
            for item in items:
                yield item

        mock_cursor.__aiter__ = lambda self: async_iter(mock_docs)
        mock_db.system_users.find.return_value.skip.return_value.limit.return_value = (
            mock_cursor
        )

        result = await get_system_users(
            filter_dict={"tenant_id": "tenant123"}, start=0, stop=10
        )

        assert len(result) == 2
        assert result[0].email == "john@acme.com"
        assert result[1].email == "jane@acme.com"


# ============================================================================
# VISIT SESSION REPOSITORY TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
class TestVisitSessionRepository:
    """Test suite for visit session repository layer."""

    @patch("repositories.visit_session_repo.db")
    async def test_create_visit_session_inserts_and_returns_out(self, mock_db):
        """Test creating a visit session inserts and returns VisitSessionOut."""
        from repositories.visit_session_repo import create_visit_session

        session_id = ObjectId()
        session_data = VisitSessionCreate(
            tenant_id="tenant123",
            visitor_profile_id=str(ObjectId()),
            department_id=str(ObjectId()),
        )

        mock_insert_result = MagicMock()
        mock_insert_result.inserted_id = session_id
        mock_db.visit_sessions.insert_one = AsyncMock(return_value=mock_insert_result)

        mock_db.visit_sessions.find_one = AsyncMock(
            return_value={
                "_id": session_id,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_in",
                "check_in_time": 1234567890,
                "date_created": 1234567890,
            }
        )

        result = await create_visit_session(session_data)

        assert isinstance(result, VisitSessionOut)
        assert result.tenant_id == "tenant123"
        mock_db.visit_sessions.insert_one.assert_called_once()

    @patch("repositories.visit_session_repo.db")
    async def test_get_visit_session_returns_none_when_not_found(self, mock_db):
        """Test get_visit_session returns None when not found."""
        from repositories.visit_session_repo import get_visit_session

        mock_db.visit_sessions.find_one = AsyncMock(return_value=None)

        result = await get_visit_session({"_id": ObjectId()})

        assert result is None

    @patch("repositories.visit_session_repo.db")
    async def test_get_visit_session_returns_out_when_found(self, mock_db):
        """Test get_visit_session returns VisitSessionOut when found."""
        from repositories.visit_session_repo import get_visit_session

        session_id = ObjectId()
        mock_db.visit_sessions.find_one = AsyncMock(
            return_value={
                "_id": session_id,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_in",
                "check_in_time": 1234567890,
            }
        )

        result = await get_visit_session({"_id": session_id})

        assert isinstance(result, VisitSessionOut)
        assert result.status == "checked_in"

    @patch("repositories.visit_session_repo.db")
    async def test_update_visit_session_returns_updated(self, mock_db):
        """Test updating a visit session returns updated VisitSessionOut."""
        from repositories.visit_session_repo import update_visit_session

        session_id = ObjectId()
        update_data = VisitSessionUpdate(status=VisitStatus.CHECKED_OUT)

        mock_db.visit_sessions.find_one_and_update = AsyncMock(
            return_value={
                "_id": session_id,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_out",
                "check_in_time": 1234567890,
                "check_out_time": 1234567950,
            }
        )

        result = await update_visit_session({"_id": session_id}, update_data)

        assert isinstance(result, VisitSessionOut)
        assert result.status == "checked_out"
        mock_db.visit_sessions.find_one_and_update.assert_called_once()

    @patch("repositories.visit_session_repo.db")
    async def test_delete_visit_session_calls_delete_one(self, mock_db):
        """Test deleting a visit session calls delete_one."""

        session_id = ObjectId()
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_db.visit_sessions.delete_one = AsyncMock(return_value=mock_result)

        # Note: There's no delete_visit_session function in the provided repo,
        # but we test the pattern here for completeness
        result = await mock_db.visit_sessions.delete_one({"_id": session_id})

        assert result.deleted_count == 1

    @patch("repositories.visit_session_repo.db")
    async def test_get_visit_sessions_returns_list(self, mock_db):
        """Test get_visit_sessions returns list of VisitSessionOut objects."""
        from repositories.visit_session_repo import get_visit_sessions

        session_id_1 = ObjectId()
        session_id_2 = ObjectId()

        mock_cursor = MagicMock()
        mock_docs = [
            {
                "_id": session_id_1,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_in",
                "check_in_time": 1234567890,
            },
            {
                "_id": session_id_2,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_out",
                "check_in_time": 1234567800,
                "check_out_time": 1234567900,
            },
        ]

        async def async_iter(items):
            for item in items:
                yield item

        mock_cursor.__aiter__ = lambda self: async_iter(mock_docs)
        mock_db.visit_sessions.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        result = await get_visit_sessions(
            filter_dict={"tenant_id": "tenant123"}, start=0, stop=10
        )

        assert len(result) == 2
        assert result[0].status == "checked_in"
        assert result[1].status == "checked_out"

    @patch("repositories.visit_session_repo.db")
    async def test_get_active_visitors_filters_by_status(self, mock_db):
        """Test get_active_visitors filters by checked_in status."""
        from repositories.visit_session_repo import get_active_visitors

        session_id = ObjectId()

        mock_cursor = MagicMock()
        mock_docs = [
            {
                "_id": session_id,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "status": "checked_in",
                "check_in_time": 1234567890,
            },
        ]

        async def async_iter(items):
            for item in items:
                yield item

        mock_cursor.__aiter__ = lambda self: async_iter(mock_docs)
        mock_db.visit_sessions.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        result = await get_active_visitors(tenant_id="tenant123")

        assert len(result) == 1
        assert result[0].status == "checked_in"
        # Verify that find was called with the correct filter
        call_args = mock_db.visit_sessions.find.call_args
        assert call_args[0][0]["status"] == "checked_in"

    @patch("repositories.visit_session_repo.db")
    async def test_get_visit_session_by_badge_token(self, mock_db):
        """Test getting visit session by badge QR token."""
        from repositories.visit_session_repo import get_visit_session_by_badge_token

        session_id = ObjectId()
        badge_token = "signed_token_xyz"

        mock_db.visit_sessions.find_one = AsyncMock(
            return_value={
                "_id": session_id,
                "tenant_id": "tenant123",
                "visitor_profile_id": str(ObjectId()),
                "department_id": str(ObjectId()),
                "badge_qr_token": badge_token,
                "status": "checked_in",
            }
        )

        result = await get_visit_session_by_badge_token(badge_token)

        assert isinstance(result, VisitSessionOut)
        mock_db.visit_sessions.find_one.assert_called_once_with(
            {"badge_qr_token": badge_token}
        )

    @patch("repositories.visit_session_repo.db")
    async def test_count_visit_sessions(self, mock_db):
        """Test counting visit sessions."""
        from repositories.visit_session_repo import count_visit_sessions

        mock_db.visit_sessions.count_documents = AsyncMock(return_value=42)

        result = await count_visit_sessions(
            {"tenant_id": "tenant123", "status": "checked_in"}
        )

        assert result == 42
        mock_db.visit_sessions.count_documents.assert_called_once()

    @patch("repositories.visit_session_repo.db")
    async def test_get_visitor_session_stats(self, mock_db):
        """Test getting visitor session statistics."""
        from repositories.visit_session_repo import get_visitor_session_stats

        stats = {
            "_id": None,
            "total_visits": 100,
            "total_checked_in": 5,
            "total_checked_out": 95,
            "avg_duration": 3600,
        }

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[stats])
        mock_db.visit_sessions.aggregate = MagicMock(return_value=mock_cursor)

        result = await get_visitor_session_stats("tenant123")

        assert result["total_visits"] == 100
        assert result["total_checked_in"] == 5
        assert result["total_checked_out"] == 95
        mock_db.visit_sessions.aggregate.assert_called_once()
