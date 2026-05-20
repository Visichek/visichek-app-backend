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
from tests.integration.conftest import complete_write, expect_write_failure


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
    from security.auth import verify_any_token

    app.dependency_overrides[_super_admin_dep] = lambda: _mock_super_admin()
    # The queued-write poll helper hits GET /v1/jobs/{job_id}, which is guarded
    # by verify_any_token (not _super_admin_dep). Override it with the same mock
    # principal so polling works without a real token.
    app.dependency_overrides[verify_any_token] = lambda: _mock_super_admin()

    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        headers={"X-Response-Case": "snake"},
    ) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_branch_crud_lifecycle(client):
    """Full CRUD lifecycle for branches."""
    ts = int(time.time() * 1000)

    # 1. Create a branch (queued → 202, committed by the worker)
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
    job = await complete_write(client, create_resp, dict(client.headers))
    branch_id = job["resource_id"]
    get_resp = await client.get(f"/v1/branches/{branch_id}")
    assert get_resp.status_code == 200, get_resp.text
    branch_data = get_resp.json()["data"]
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
    job2 = await complete_write(client, create_resp2, dict(client.headers))
    branch2_id = job2["resource_id"]

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
    await complete_write(client, update_resp, dict(client.headers))
    get_after_update = await client.get(f"/v1/branches/{branch_id}")
    assert get_after_update.status_code == 200
    assert get_after_update.json()["data"]["city"] == "Updated City"

    # 6. Deactivate second branch (first one stays active)
    deactivate_resp = await client.post(f"/v1/branches/{branch2_id}/deactivate")
    await complete_write(client, deactivate_resp, dict(client.headers))
    get_after_deactivate = await client.get(f"/v1/branches/{branch2_id}")
    assert get_after_deactivate.status_code == 200
    assert get_after_deactivate.json()["data"]["status"] == "inactive"

    # 7. Delete second branch (worker commits the delete)
    delete_resp = await client.delete(f"/v1/branches/{branch2_id}")
    await complete_write(client, delete_resp, dict(client.headers))


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
    job = await complete_write(client, resp, dict(client.headers))
    branch_id = job["resource_id"]

    # Try to delete it — the route enqueues (202) and the worker rejects it
    # because a tenant must keep at least one branch.
    del_resp = await client.delete(f"/v1/branches/{branch_id}")
    await expect_write_failure(client, del_resp, dict(client.headers))


@pytest.mark.asyncio
async def test_duplicate_branch_name_fails(client):
    """Creating two branches with the same name in a tenant should fail."""
    ts = int(time.time() * 1000)
    name = f"Dupe-{ts}"

    resp1 = await client.post(
        "/v1/branches",
        json={"tenant_id": "integration-tenant-1", "name": name},
    )
    # Commit the first branch before posting the duplicate so the worker sees
    # it (concurrency could otherwise let both run before either commits).
    await complete_write(client, resp1, dict(client.headers))

    resp2 = await client.post(
        "/v1/branches",
        json={"tenant_id": "integration-tenant-1", "name": name},
    )
    await expect_write_failure(client, resp2, dict(client.headers))
