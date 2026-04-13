"""
Integration tests for the branch lifecycle.

Tests the full flow: create branch → list → update → deactivate → delete.
Requires a running MongoDB + Redis instance.

Run with:
    pytest tests/integration/test_branch_lifecycle.py -v
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorDatabase

from main import app
from api.v1.branch_route import _super_admin_dep


def _mock_super_admin(tenant_id: str = "integration-tenant-1"):
    """Return a mock AuthPrincipal for super_admin."""
    principal = MagicMock()
    principal.user_id = "sa-user-1"
    principal.role = "super_admin"
    principal.tenant_id = tenant_id
    principal.access_token_id = "tok-1"
    principal.jwt_token = "fake-jwt"
    return principal


@pytest_asyncio.fixture
async def client(mongo_db: AsyncIOMotorDatabase):
    """Async HTTP client with super_admin auth overridden.

    Depends on mongo_db to ensure the database connection is patched
    to the current event loop before any requests hit the app.
    """
    app.dependency_overrides[_super_admin_dep] = lambda: _mock_super_admin()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_branch_crud_lifecycle(client):
    """Full CRUD lifecycle for branches."""
    ts = int(time.time() * 1000)

    # 1. Create a branch
    create_resp = await client.post(
        "/v1/branches",
        json={
            "tenant_id": "integration-tenant-1",
            "name": f"Branch-{ts}",
            "address": "123 Test Street",
            "city": "Lagos",
            "country": "Nigeria",
            "is_headquarters": True,
        },
    )
    assert create_resp.status_code in (200, 201), create_resp.text
    branch_data = create_resp.json()["data"]
    branch_id = branch_data["id"]
    assert branch_data["name"] == f"Branch-{ts}"
    assert branch_data["is_headquarters"] is True

    # 2. Create a second branch
    create_resp2 = await client.post(
        "/v1/branches",
        json={
            "tenant_id": "integration-tenant-1",
            "name": f"Branch-2-{ts}",
            "city": "Abuja",
        },
    )
    assert create_resp2.status_code in (200, 201)
    branch2_id = create_resp2.json()["data"]["id"]

    # 3. List branches
    list_resp = await client.get("/v1/branches")
    assert list_resp.status_code == 200
    branches = list_resp.json()["data"]
    assert len(branches) >= 2

    # 4. Get single branch
    get_resp = await client.get(f"/v1/branches/{branch_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == branch_id

    # 5. Update branch
    update_resp = await client.put(
        f"/v1/branches/{branch_id}",
        json={"city": "Updated City", "phone": "+234800000"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["city"] == "Updated City"

    # 6. Deactivate second branch (first one stays active)
    deactivate_resp = await client.post(f"/v1/branches/{branch2_id}/deactivate")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["data"]["status"] == "inactive"

    # 7. Delete second branch
    delete_resp = await client.delete(f"/v1/branches/{branch2_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["data"]["deleted"] is True


@pytest.mark.asyncio
async def test_cannot_delete_last_branch(client):
    """Deleting the last branch should fail with 400."""
    ts = int(time.time() * 1000)

    # Create only one branch
    resp = await client.post(
        "/v1/branches",
        json={
            "tenant_id": "integration-tenant-1",
            "name": f"Solo-{ts}",
        },
    )
    assert resp.status_code in (200, 201)
    branch_id = resp.json()["data"]["id"]

    # Try to delete it
    del_resp = await client.delete(f"/v1/branches/{branch_id}")
    # Should fail because it's the last branch
    assert del_resp.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_branch_name_fails(client):
    """Creating two branches with the same name in a tenant should fail."""
    ts = int(time.time() * 1000)
    name = f"Dupe-{ts}"

    resp1 = await client.post(
        "/v1/branches",
        json={"tenant_id": "integration-tenant-1", "name": name},
    )
    assert resp1.status_code in (200, 201)

    resp2 = await client.post(
        "/v1/branches",
        json={"tenant_id": "integration-tenant-1", "name": name},
    )
    assert resp2.status_code == 409
