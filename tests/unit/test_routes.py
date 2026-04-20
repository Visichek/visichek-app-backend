from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import (
    verify_super_admin_token,
    verify_system_user_token,
    verify_any_system_user_token,
)


# Mock AuthPrincipal instances for different roles
MOCK_SUPER_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="super-admin-123",
    role="super_admin",
    access_token_id="token-123",
    jwt_token="jwt-token-123",
    tenant_id="tenant-001",
)

MOCK_DEPT_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="dept-admin-456",
    role="dept_admin",
    access_token_id="token-456",
    jwt_token="jwt-token-456",
    tenant_id="tenant-001",
)

MOCK_RECEPTIONIST_PRINCIPAL = AuthPrincipal(
    user_id="receptionist-789",
    role="receptionist",
    access_token_id="token-789",
    jwt_token="jwt-token-789",
    tenant_id="tenant-001",
)

MOCK_SECURITY_OFFICER_PRINCIPAL = AuthPrincipal(
    user_id="security-officer-999",
    role="security_officer",
    access_token_id="token-999",
    jwt_token="jwt-token-999",
    tenant_id="tenant-001",
)

MOCK_DPO_PRINCIPAL = AuthPrincipal(
    user_id="dpo-111",
    role="dpo",
    access_token_id="token-111",
    jwt_token="jwt-token-111",
    tenant_id="tenant-001",
)


# Mock AdminOut for application admin auth dependency
MOCK_ADMIN_OUT = {
    "id": "admin-legacy-001",
    "full_name": "Application Admin",
    "email": "legacy@example.com",
    "password": "",
    "accountStatus": "ACTIVE",
    "permissionList": {
        "permissions": [
            {
                "name": "all",
                "methods": ["GET", "POST", "PATCH", "DELETE"],
                "path": "/v1/*",
                "key": "all_access",
            }
        ]
    },
    "date_created": 1712500000,
    "last_updated": 1712500000,
}


@pytest.fixture
def cleanup_dependency_overrides():
    """Cleanup dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


class TestTenantRoutes:
    """Tests for tenant-related endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_tenant_success(self, cleanup_dependency_overrides):
        """Tenant writes go through the queue — route returns 202 + job envelope."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        with patch(
            "api.v1.tenant_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "tenant-001",
                "job_id": "job-tnt-abc",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/tenants",
                    json={
                        "company_name": "Acme Corp",
                        "lawful_basis": "legitimate_interest",
                        "notice_display_mode": "passive",
                        "retention_days": 1095,
                        "default_retention_action": "anonymise",
                        "dpo_contact_email": "dpo@acmecorp.com",
                        "privacy_policy_url": "https://acmecorp.com/privacy",
                        "country_of_hosting": "United States",
                        "cross_border_approved": False,
                    },
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["data"]["id"] == "tenant-001"
            assert data["data"]["jobId"] == "job-tnt-abc"
            assert data["data"]["status"] == "queued"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "tenant.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_tenants_success(self, cleanup_dependency_overrides):
        """Test successful tenant listing."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        with patch(
            "api.v1.tenant_route.retrieve_tenants_with_summary", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "tenant-001",
                    "company_name": "Acme Corp",
                    "is_active": True,
                    "date_created": 1712500000,
                },
                {
                    "id": "tenant-002",
                    "company_name": "Tech Inc",
                    "is_active": True,
                    "date_created": 1712500100,
                },
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/tenants?start=0&stop=100",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Tenants fetched successfully"
            assert len(data["data"]) == 2
            assert "meta" in data or "success" in data

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_tenant_success(self, cleanup_dependency_overrides):
        """Test retrieving a specific tenant by ID."""
        from security.auth import verify_any_token

        app.dependency_overrides[verify_any_token] = lambda: MOCK_SUPER_ADMIN_PRINCIPAL

        with patch(
            "api.v1.tenant_route.retrieve_tenant_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "tenant-001",
                "company_name": "Acme Corp",
                "is_active": True,
                "date_created": 1712500000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/tenants/tenant-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["id"] == "tenant-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_tenant_not_found(self, cleanup_dependency_overrides):
        """Test retrieving a non-existent tenant."""
        from security.auth import verify_any_token

        app.dependency_overrides[verify_any_token] = lambda: MOCK_SUPER_ADMIN_PRINCIPAL

        with patch(
            "api.v1.tenant_route.retrieve_tenant_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            from core.errors import AppException, ErrorCode
            from fastapi import status

            mock_get.side_effect = AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="Tenant not found",
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/tenants/nonexistent-id",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code in (403, 404)
            data = response.json()
            assert data.get("success") is False or "detail" in data

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_tenant_success(self, cleanup_dependency_overrides):
        """Tenant updates go through the queue — route returns 202 + job envelope."""
        app.dependency_overrides[verify_super_admin_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.tenant_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "tenant-001",
                "job_id": "job-tnt-upd",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/tenants/tenant-001",
                    json={"company_name": "Acme Corp Updated"},
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["id"] == "tenant-001"
            assert data["data"]["jobId"] == "job-tnt-upd"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "tenant.update"
            assert mock_enqueue.await_args.kwargs["resource_id"] == "tenant-001"


class TestDepartmentRoutes:
    """Tests for department-related endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_department_success(self, cleanup_dependency_overrides):
        """Writes go through the queue pipeline now — route returns 202 + job envelope."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.department_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "dept-001",
                "job_id": "job-abc-123",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/departments",
                    json={
                        "tenant_id": "tenant-001",
                        "code": "HR-001",
                        "name": "Human Resources",
                    },
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Department creation queued"
            assert data["data"]["id"] == "dept-001"
            assert data["data"]["jobId"] == "job-abc-123"
            assert data["data"]["status"] == "queued"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "department.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_departments_success(self, cleanup_dependency_overrides):
        """Test successful department listing."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.department_route.retrieve_departments_with_summary",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "dept-001",
                    "tenant_id": "tenant-001",
                    "code": "HR-001",
                    "name": "Human Resources",
                    "is_active": True,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/departments?start=0&stop=100",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_department_success(self, cleanup_dependency_overrides):
        """Test retrieving a specific department."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.department_route.retrieve_department_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "dept-001",
                "tenant_id": "tenant-001",
                "code": "HR-001",
                "name": "Human Resources",
                "is_active": True,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/departments/dept-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["id"] == "dept-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_department_success(self, cleanup_dependency_overrides):
        """Updates go through the queue pipeline now — route returns 202 + job envelope."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.department_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "dept-001",
                "job_id": "job-upd-456",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/departments/dept-001",
                    json={"name": "Human Resources - Updated"},
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["id"] == "dept-001"
            assert data["data"]["jobId"] == "job-upd-456"
            assert data["data"]["status"] == "queued"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "department.update"
            assert mock_enqueue.await_args.kwargs["resource_id"] == "dept-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_delete_department_success(self, cleanup_dependency_overrides):
        """Deletes go through the queue pipeline now — route returns 202 + job envelope."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.department_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "dept-001",
                "job_id": "job-del-789",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(
                    "/v1/departments/dept-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["data"]["id"] == "dept-001"
            assert data["data"]["jobId"] == "job-del-789"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "department.delete"


class TestSystemUserRoutes:
    """Tests for system user authentication and management endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_login_success(self, cleanup_dependency_overrides):
        """Test successful system user login."""
        with patch(
            "api.v1.system_user_route.authenticate_system_user",
            new_callable=AsyncMock,
        ) as mock_auth:
            mock_auth.return_value = {
                "id": "user-123",
                "tenant_id": "tenant-001",
                "full_name": "Dr. Sarah Wilson",
                "email": "sarah.wilson@clinic.example.com",
                "role": "receptionist",
                "account_status": "ACTIVE",
                "is_active": True,
                "access_token": "access-token-123",
                "refresh_token": "refresh-token-123",
                "last_login_at": 1712520000,
                "date_created": 1712500000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/system-users/login",
                    json={
                        "email": "sarah.wilson@clinic.example.com",
                        "password": "secure_password",
                    },
                    headers={"X-Auth-Include-Tokens": "true"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Login successful"
            assert (
                data["data"].get("access_token") or data["data"].get("accessToken")
            ) == "access-token-123"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_login_invalid_credentials(self, cleanup_dependency_overrides):
        """Test login with invalid credentials."""
        with patch(
            "api.v1.system_user_route.authenticate_system_user",
            new_callable=AsyncMock,
        ) as mock_auth:
            from core.errors import AppException, ErrorCode
            from fastapi import status

            mock_auth.side_effect = AppException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=ErrorCode.AUTH_INVALID_TOKEN,
                message="Invalid email or password",
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/system-users/login",
                    json={
                        "email": "sarah.wilson@clinic.example.com",
                        "password": "wrong_password",
                    },
                )

            assert response.status_code == 401
            data = response.json()
            assert data["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_signup_success(self, cleanup_dependency_overrides):
        """Test successful system user signup."""
        app.dependency_overrides[verify_super_admin_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.system_user_route.add_system_user_from_invite",
            new_callable=AsyncMock,
        ) as mock_add:
            mock_add.return_value = {
                "id": "user-124",
                "tenant_id": "tenant-001",
                "full_name": "Dr. Michael Brown",
                "email": "michael.brown@clinic.example.com",
                "role": "dept_admin",
                "account_status": "ACTIVE",
                "is_active": True,
                "date_created": 1712521000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/system-users/signup",
                    json={
                        "tenant_id": "tenant-001",
                        "full_name": "Dr. Michael Brown",
                        "email": "michael.brown@clinic.example.com",
                        "password": "secure_password",
                        "role": "dept_admin",
                    },
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "System user created successfully"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_system_users_success(self, cleanup_dependency_overrides):
        """Test listing system users."""
        app.dependency_overrides[verify_super_admin_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.system_user_route.retrieve_system_users", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "user-123",
                    "tenant_id": "tenant-001",
                    "full_name": "Dr. Sarah Wilson",
                    "email": "sarah.wilson@clinic.example.com",
                    "role": "receptionist",
                    "is_active": True,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/system-users?start=0&stop=100",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_my_profile(self, cleanup_dependency_overrides):
        """Test retrieving authenticated user profile."""
        app.dependency_overrides[verify_any_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.system_user_route.retrieve_system_user_by_id",
            new_callable=AsyncMock,
        ) as mock_get:
            from schemas.system_user_schema import SystemUserOut
            from schemas.imports import SystemUserRole

            mock_get.return_value = SystemUserOut(
                id="receptionist-789",
                tenant_id="tenant-001",
                full_name="John Receptionist",
                email="john@clinic.example.com",
                role=SystemUserRole.RECEPTIONIST,
                is_active=True,
            )

            with patch(
                "api.v1.system_user_route.retrieve_tenant_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        "/v1/system-users/me",
                        headers={"Authorization": "Bearer token-789"},
                    )

            assert response.status_code == 200
            data = response.json()
            # Response shape may include user wrapper or tenant nested; case-conversion
            # middleware may map "id" to "Id" (edge case with short keys).
            flat_id = (
                data["data"].get("id")
                or data["data"].get("Id")
                or data["data"].get("user", {}).get("id")
            )
            assert flat_id == "receptionist-789"


class TestVisitorRoutes:
    """Tests for visitor check-in/check-out endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_check_in_visitor_success(self, cleanup_dependency_overrides):
        """Test successful visitor check-in."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.check_in_visitor", new_callable=AsyncMock
        ) as mock_checkin:
            mock_checkin.return_value = {
                "id": "visit-001",
                "tenant_id": "tenant-001",
                "visitor_name_snapshot": "John Doe",
                "status": "checked_in",
                "check_in_time": 1712532000,
                "check_out_time": None,
                "purpose": "Business meeting",
                "date_created": 1712532000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/check-in",
                    json={
                        "full_name": "John Doe",
                        "phone": "555-0123",
                        "purpose": "Business meeting",
                        "department_id": "dept-001",
                        "host_id": "user-123",
                        "check_in_method": "manual_entry",
                    },
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Visitor checked in successfully"
            assert data["data"]["status"] == "checked_in"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_check_out_visitor_success(self, cleanup_dependency_overrides):
        """Test successful visitor check-out."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.check_out_visitor", new_callable=AsyncMock
        ) as mock_checkout:
            mock_checkout.return_value = {
                "id": "visit-001",
                "tenant_id": "tenant-001",
                "visitor_name_snapshot": "John Doe",
                "status": "checked_out",
                "check_in_time": 1712532000,
                "check_out_time": 1712535600,
                "visit_duration": 3600,
                "date_created": 1712532000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/check-out",
                    json={"session_id": "visit-001"},
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Visitor checked out successfully"
            assert data["data"]["status"] == "checked_out"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_active_visitors(self, cleanup_dependency_overrides):
        """Test listing active visitors."""
        app.dependency_overrides[verify_any_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.retrieve_active_visitors",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "visit-001",
                    "visitor_name_snapshot": "John Doe",
                    "status": "checked_in",
                    "check_in_time": 1712532000,
                    "department_id": "dept-001",
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/active",
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_visit_session(self, cleanup_dependency_overrides):
        """Test retrieving a specific visit session."""
        app.dependency_overrides[verify_any_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.retrieve_visit_session_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "visit-001",
                "tenant_id": "tenant-001",
                "visitor_name_snapshot": "John Doe",
                "status": "checked_out",
                "check_in_time": 1712532000,
                "check_out_time": 1712535600,
                "visit_duration": 3600,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/sessions/visit-001",
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["id"] == "visit-001"
            assert data["data"]["status"] == "checked_out"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_visit_sessions(self, cleanup_dependency_overrides):
        """Test listing visit sessions with pagination."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_DEPT_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.retrieve_visit_sessions_with_summary",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "visit-001",
                    "visitor_name_snapshot": "John Doe",
                    "status": "checked_out",
                    "check_in_time": 1712532000,
                    "check_out_time": 1712535600,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/sessions?start=0&stop=100",
                    headers={"Authorization": "Bearer token-456"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1


class TestAppointmentRoutes:
    """Tests for appointment-related endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_appointment_success(self, cleanup_dependency_overrides):
        """Test successful appointment creation."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.appointment_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "appt-001",
                "job_id": "job-appt-create",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/appointments",
                    json={
                        "tenant_id": "tenant-001",
                        "visitor_profile_id": "visitor-001",
                        "host_id": "user-123",
                        "department_id": "dept-001",
                        "scheduled_datetime": 1712618400,
                        "purpose": "Sales consultation",
                    },
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["id"] == "appt-001"
            assert data["data"]["jobId"] == "job-appt-create"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "appointment.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_appointments_success(self, cleanup_dependency_overrides):
        """Test listing appointments."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.appointment_route.retrieve_appointments_with_summary",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "appt-001",
                    "tenant_id": "tenant-001",
                    "visitor_name_snapshot": "John Doe",
                    "host_name_snapshot": "Jane Smith",
                    "scheduled_datetime": 1712618400,
                    "purpose": "Sales consultation",
                    "status": "scheduled",
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/appointments?start=0&stop=100",
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_appointment_success(self, cleanup_dependency_overrides):
        """Test retrieving a specific appointment."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.appointment_route.retrieve_appointment_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "appt-001",
                "tenant_id": "tenant-001",
                "visitor_name_snapshot": "John Doe",
                "scheduled_datetime": 1712618400,
                "purpose": "Sales consultation",
                "status": "scheduled",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/appointments/appt-001",
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["id"] == "appt-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_appointment_success(self, cleanup_dependency_overrides):
        """Test successful appointment update."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.appointment_route.enqueue_write",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "appt-001",
                "job_id": "job-appt-upd",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/appointments/appt-001",
                    json={"scheduled_datetime": 1712704800},
                    headers={"Authorization": "Bearer token-789"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-appt-upd"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "appointment.update"
            assert mock_enqueue.await_args.kwargs["resource_id"] == "appt-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_delete_appointment_success(self, cleanup_dependency_overrides):
        """Test successful appointment deletion."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_DEPT_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.appointment_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "appt-001",
                "job_id": "job-appt-del",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(
                    "/v1/appointments/appt-001",
                    headers={"Authorization": "Bearer token-456"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-appt-del"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "appointment.delete"


class TestPrivacyNoticeRoutes:
    """Tests for privacy notice endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_privacy_notice_success(self, cleanup_dependency_overrides):
        """Test successful privacy notice creation."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.privacy_notice_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "notice-001",
                "job_id": "job-notice-create",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/privacy-notices",
                    json={
                        "tenant_id": "tenant-001",
                        "version_code": "v2.1",
                        "title": "Data Processing Notice",
                        "summary": "Information about how we process your personal data",
                        "full_policy_url": "https://example.com/privacy-policy",
                        "effective_from": 1712448000,
                    },
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-notice-create"
            mock_enqueue.assert_awaited_once()
            assert (
                mock_enqueue.await_args.kwargs["writer_key"] == "privacy_notice.create"
            )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_active_privacy_notice(self, cleanup_dependency_overrides):
        """Test retrieving active privacy notice."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.privacy_notice_route.retrieve_active_notice",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "notice-001",
                "tenant_id": "tenant-001",
                "version_code": "v2.1",
                "title": "Data Processing Notice",
                "is_active": True,
                "date_created": 1712448000,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/privacy-notices/active",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert (
                data["data"].get("is_active") or data["data"].get("isActive")
            ) is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_privacy_notices(self, cleanup_dependency_overrides):
        """Test listing privacy notices."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.privacy_notice_route.retrieve_privacy_notices",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "notice-001",
                    "tenant_id": "tenant-001",
                    "version_code": "v2.1",
                    "title": "Data Processing Notice",
                    "is_active": True,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/privacy-notices?start=0&stop=100",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_privacy_notice_success(self, cleanup_dependency_overrides):
        """Test successful privacy notice update."""
        app.dependency_overrides[verify_system_user_token] = lambda: MOCK_DPO_PRINCIPAL

        with patch(
            "api.v1.privacy_notice_route.enqueue_write",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "notice-001",
                "job_id": "job-notice-upd",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/privacy-notices/notice-001",
                    json={"title": "Data Processing Notice - Updated"},
                    headers={"Authorization": "Bearer token-111"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-notice-upd"
            mock_enqueue.assert_awaited_once()
            assert (
                mock_enqueue.await_args.kwargs["writer_key"] == "privacy_notice.update"
            )


class TestIncidentRoutes:
    """Tests for incident-related endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_incident_success(self, cleanup_dependency_overrides):
        """Test successful incident creation."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SECURITY_OFFICER_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "incident-001",
                "job_id": "job-inc-create",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/incidents",
                    json={
                        "tenant_id": "tenant-001",
                        "reported_by": "security-officer-999",
                        "incident_type": "data_breach",
                        "description": "Unauthorized access detected",
                        "risk_level": "high",
                        "detection_time": 1712544600,
                    },
                    headers={"Authorization": "Bearer token-999"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-inc-create"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "incident.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_incidents_success(self, cleanup_dependency_overrides):
        """Test listing incidents."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SECURITY_OFFICER_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.retrieve_incidents", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "incident-001",
                    "tenant_id": "tenant-001",
                    "incident_type": "data_breach",
                    "status": "open",
                    "risk_level": "high",
                    "date_created": 1712544800,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/incidents?start=0&stop=100",
                    headers={"Authorization": "Bearer token-999"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_incident_success(self, cleanup_dependency_overrides):
        """Test retrieving a specific incident."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.retrieve_incident_by_id", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = {
                "id": "incident-001",
                "tenant_id": "tenant-001",
                "incident_type": "data_breach",
                "status": "investigating",
                "risk_level": "high",
                "description": "Unauthorized access detected",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/incidents/incident-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["id"] == "incident-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_incident_success(self, cleanup_dependency_overrides):
        """Test successful incident update."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SECURITY_OFFICER_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "incident-001",
                "job_id": "job-inc-upd",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/incidents/incident-001",
                    json={
                        "status": "reported_to_ndpc",
                        "ndpc_notified": True,
                        "ndpc_notified_at": 1712548400,
                    },
                    headers={"Authorization": "Bearer token-999"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-inc-upd"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "incident.update"
            assert mock_enqueue.await_args.kwargs["resource_id"] == "incident-001"


class TestErrorHandling:
    """Tests for error handling and response envelope format."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_unauthorized_request_without_token(self):
        """Test that unauthorized requests without tokens return proper error."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v1/tenants")

            assert response.status_code in (401, 403)
            data = response.json()
            assert data.get("success") is False or "detail" in data

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_validation_error_response_format(self, cleanup_dependency_overrides):
        """Test that validation errors return proper envelope format."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/system-users/login",
                json={"email": "test@example.com"},  # Missing password
            )

            assert response.status_code == 422
            data = response.json()
            assert "success" in data or "detail" in data


class TestResponseEnvelopeFormat:
    """Tests for standard response envelope format."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_success_response_envelope(self, cleanup_dependency_overrides):
        """Test that successful responses have proper envelope format."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        with patch(
            "api.v1.tenant_route.retrieve_tenants_with_summary", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = []

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/tenants",
                    headers={"Authorization": "Bearer token-123"},
                )

            data = response.json()
            assert "success" in data
            assert "message" in data
            assert "data" in data
            assert isinstance(data["success"], bool)
            assert data["success"] is True


# ============================================================================
# BOOTSTRAP ROUTE TESTS
# ============================================================================


class TestBootstrapRoute:
    """Tests for the POST /admins/tenants/bootstrap endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_bootstrap_success(self, cleanup_dependency_overrides):
        """Test successful tenant bootstrap by application admin."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        mock_result = {
            "tenant": {
                "id": "tenant-new-001",
                "company_name": "Bootstrap Corp",
                "lawful_basis": "legitimate_interest",
                "notice_display_mode": "passive",
                "retention_days": 1095,
                "default_retention_action": "anonymise",
                "is_active": True,
                "date_created": 1712500000,
                "last_updated": 1712500000,
            },
            "super_admin": {
                "id": "su-new-001",
                "tenant_id": "tenant-new-001",
                "full_name": "Jane Doe",
                "email": "jane@bootstrapcorp.com",
                "role": "super_admin",
                "account_status": "ACTIVE",
                "access_token": "eyJ...",
                "refresh_token": "eyJ...",
                "date_created": 1712500000,
                "last_updated": 1712500000,
            },
        }

        with patch(
            "api.v1.admin_route.bootstrap_tenant", new_callable=AsyncMock
        ) as mock_bootstrap:
            mock_bootstrap.return_value = mock_result

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/admins/tenants/bootstrap",
                    json={
                        "company_name": "Bootstrap Corp",
                        "admin_full_name": "Jane Doe",
                        "admin_email": "jane@bootstrapcorp.com",
                        "admin_password": "SecurePass123!",
                    },
                    headers={"Authorization": "Bearer admin-token"},
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Tenant and super admin created successfully"
            assert data["data"]["tenant"]["id"] == "tenant-new-001"
            sa = data["data"].get("super_admin") or data["data"].get("superAdmin")
            assert sa is not None and sa["role"] == "super_admin"
            mock_bootstrap.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_bootstrap_duplicate_company(self, cleanup_dependency_overrides):
        """Test bootstrap returns 409 on duplicate company name."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        from fastapi import HTTPException as FastAPIHTTPException

        with patch(
            "api.v1.admin_route.bootstrap_tenant", new_callable=AsyncMock
        ) as mock_bootstrap:
            mock_bootstrap.side_effect = FastAPIHTTPException(
                status_code=409,
                detail="Tenant with this company name already exists",
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/admins/tenants/bootstrap",
                    json={
                        "company_name": "Existing Corp",
                        "admin_full_name": "Jane Doe",
                        "admin_email": "jane@existing.com",
                        "admin_password": "Pass123!",
                    },
                    headers={"Authorization": "Bearer admin-token"},
                )

            assert response.status_code == 409

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_bootstrap_validation_error(self, cleanup_dependency_overrides):
        """Test bootstrap returns 422 on missing required fields."""
        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN_OUT
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/admins/tenants/bootstrap",
                json={
                    "company_name": "Incomplete Corp",
                    # missing admin_full_name, admin_email, admin_password
                },
                headers={"Authorization": "Bearer admin-token"},
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_bootstrap_unauthenticated(self, cleanup_dependency_overrides):
        """Test bootstrap without auth token is rejected."""
        # Don't override the dependency — let real auth run and fail
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/admins/tenants/bootstrap",
                json={
                    "company_name": "No Auth Corp",
                    "admin_full_name": "Jane Doe",
                    "admin_email": "jane@noauth.com",
                    "admin_password": "Pass123!",
                },
            )

        assert response.status_code in (401, 403)
