from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from security.auth import verify_system_user_token


MOCK_SUPER_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="super-admin-123",
    role="super_admin",
    access_token_id="token-123",
    jwt_token="jwt-token-123",
    tenant_id="tenant-001",
)


@pytest.fixture
def cleanup_dependency_overrides():
    yield
    app.dependency_overrides.clear()


class TestHostRoutes:
    """Tests for host-related endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_host_success(self, cleanup_dependency_overrides):
        """Writes go through the queue pipeline — route returns 202 + job envelope."""
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.host_route.validate_host_create", new_callable=AsyncMock
        ), patch(
            "api.v1.host_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "host-001",
                "job_id": "job-abc-123",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/hosts",
                    json={
                        "name": "Jane Host",
                        "phone": "+2348012345678",
                        "department_id": "64f1a2b3c4d5e6f7a8b9c0d1",
                    },
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Host creation queued"
            assert data["data"]["id"] == "host-001"
            assert data["data"]["jobId"] == "job-abc-123"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "host.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_hosts_success(self, cleanup_dependency_overrides):
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.host_route.retrieve_hosts_with_summary", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = [
                {
                    "id": "host-001",
                    "tenant_id": "tenant-001",
                    "name": "Jane Host",
                    "phone": "+2348012345678",
                    "department_id": "64f1a2b3c4d5e6f7a8b9c0d1",
                    "is_active": True,
                }
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No query params → default-listing branch, served by the
                # mocked retrieve_hosts_with_summary loader. A `?q=...` here would
                # take the parse_list_query/run_list path against the real
                # db.hosts collection, which a unit test must not touch.
                response = await client.get(
                    "/v1/hosts",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_host_success(self, cleanup_dependency_overrides):
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.host_route.retrieve_host_by_id_with_summary",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "id": "host-001",
                "tenant_id": "tenant-001",
                "name": "Jane Host",
                "phone": "+2348012345678",
                "department_id": "64f1a2b3c4d5e6f7a8b9c0d1",
                "is_active": True,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/hosts/host-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["id"] == "host-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_host_success(self, cleanup_dependency_overrides):
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.host_route.validate_host_update", new_callable=AsyncMock
        ), patch(
            "api.v1.host_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "host-001",
                "job_id": "job-upd-456",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/v1/hosts/host-001",
                    json={"name": "Jane Updated", "phone": "+2348099999999"},
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["id"] == "host-001"
            assert data["data"]["jobId"] == "job-upd-456"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "host.update"
            assert mock_enqueue.await_args.kwargs["resource_id"] == "host-001"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_delete_host_success(self, cleanup_dependency_overrides):
        app.dependency_overrides[verify_system_user_token] = lambda: (
            MOCK_SUPER_ADMIN_PRINCIPAL
        )

        with patch(
            "api.v1.host_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "host-001",
                "job_id": "job-del-789",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(
                    "/v1/hosts/host-001",
                    headers={"Authorization": "Bearer token-123"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["id"] == "host-001"
            assert data["data"]["jobId"] == "job-del-789"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "host.delete"
