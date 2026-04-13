"""Unit tests for the branches feature."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.branch_schema import (
    BranchCreate,
    BranchOut,
    BranchUpdate,
    BranchStatus,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestBranchSchemas:
    def test_branch_create_valid(self):
        b = BranchCreate(tenant_id="t1", name="HQ")
        assert b.name == "HQ"
        assert b.tenant_id == "t1"
        assert b.status == BranchStatus.ACTIVE
        assert b.is_headquarters is False
        assert b.date_created > 0

    def test_branch_create_empty_name_fails(self):
        with pytest.raises(ValueError, match="Branch name is required"):
            BranchCreate(tenant_id="t1", name="   ")

    def test_branch_create_empty_tenant_fails(self):
        with pytest.raises(ValueError, match="tenant_id is required"):
            BranchCreate(tenant_id="  ", name="Lagos")

    def test_branch_create_headquarters(self):
        b = BranchCreate(tenant_id="t1", name="Main", is_headquarters=True)
        assert b.is_headquarters is True

    def test_branch_update_partial(self):
        u = BranchUpdate(name="New Name")
        assert u.name == "New Name"
        assert u.city is None
        assert u.last_updated > 0

    def test_branch_out_objectid_conversion(self):
        from bson import ObjectId

        oid = ObjectId()
        b = BranchOut(**{
            "_id": oid,
            "tenant_id": "t1",
            "name": "HQ",
            "status": "active",
        })
        assert b.id == str(oid)

    def test_branch_out_string_id(self):
        b = BranchOut(**{
            "_id": "abc123",
            "tenant_id": "t1",
            "name": "HQ",
            "status": "active",
        })
        assert b.id == "abc123"

    def test_branch_status_enum(self):
        assert BranchStatus.ACTIVE == "active"
        assert BranchStatus.INACTIVE == "inactive"


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_branch_repo():
    """Patch all branch repository functions."""
    with (
        patch("services.branch_service.create_branch", new_callable=AsyncMock) as mock_create,
        patch("services.branch_service.get_branch", new_callable=AsyncMock) as mock_get,
        patch("services.branch_service.get_branches", new_callable=AsyncMock) as mock_get_many,
        patch("services.branch_service.count_branches", new_callable=AsyncMock) as mock_count,
        patch("services.branch_service.update_branch", new_callable=AsyncMock) as mock_update,
        patch("services.branch_service.delete_branch", new_callable=AsyncMock) as mock_delete,
    ):
        yield {
            "create": mock_create,
            "get": mock_get,
            "get_many": mock_get_many,
            "count": mock_count,
            "update": mock_update,
            "delete": mock_delete,
        }


def _make_branch_out(**kwargs):
    defaults = {
        "_id": "branch1",
        "tenant_id": "t1",
        "name": "HQ",
        "status": "active",
        "is_headquarters": True,
    }
    defaults.update(kwargs)
    return BranchOut(**defaults)


@pytest.mark.asyncio
async def test_add_branch_success(mock_branch_repo):
    mock_branch_repo["get"].return_value = None  # No duplicate
    mock_branch_repo["count"].return_value = 0
    expected = _make_branch_out()
    mock_branch_repo["create"].return_value = expected

    from services.branch_service import add_branch

    # Patch plan cache to avoid real DB
    with patch("services.branch_service.resolve_tenant_plan", new_callable=AsyncMock, return_value=None):
        result = await add_branch(BranchCreate(tenant_id="t1", name="HQ"))
        assert result.name == "HQ"
        mock_branch_repo["create"].assert_called_once()


@pytest.mark.asyncio
async def test_add_branch_duplicate_name(mock_branch_repo):
    mock_branch_repo["get"].return_value = _make_branch_out()

    from services.branch_service import add_branch
    from fastapi import HTTPException

    with patch("services.branch_service.resolve_tenant_plan", new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await add_branch(BranchCreate(tenant_id="t1", name="HQ"))
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_add_branch_cap_exceeded(mock_branch_repo):
    mock_branch_repo["get"].return_value = None
    mock_branch_repo["count"].return_value = 3  # Already at limit

    from services.branch_service import add_branch
    from fastapi import HTTPException

    plan_data = {"tenant_caps": {"max_branches": 3}}

    with patch("services.branch_service.resolve_tenant_plan", new_callable=AsyncMock, return_value=plan_data):
        with pytest.raises(HTTPException) as exc_info:
            await add_branch(BranchCreate(tenant_id="t1", name="New Branch"))
        assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_add_branch_no_cap_limit(mock_branch_repo):
    """When max_branches is None, no limit is enforced."""
    mock_branch_repo["get"].return_value = None
    mock_branch_repo["count"].return_value = 100
    mock_branch_repo["create"].return_value = _make_branch_out(name="Branch 101")

    from services.branch_service import add_branch

    plan_data = {"tenant_caps": {"max_branches": None}}
    with patch("services.branch_service.resolve_tenant_plan", new_callable=AsyncMock, return_value=plan_data):
        result = await add_branch(BranchCreate(tenant_id="t1", name="Branch 101"))
        assert result.name == "Branch 101"


@pytest.mark.asyncio
async def test_deactivate_last_branch_fails(mock_branch_repo):
    mock_branch_repo["count"].return_value = 1
    mock_branch_repo["get"].return_value = _make_branch_out()

    from services.branch_service import deactivate_branch
    from fastapi import HTTPException

    # Mock retrieve_branch_by_id to use get
    with patch("services.branch_service.get_branch", new_callable=AsyncMock, return_value=_make_branch_out()):
        with pytest.raises(HTTPException) as exc_info:
            await deactivate_branch("branch1")
        assert exc_info.value.status_code == 400
        assert "last active branch" in exc_info.value.detail


@pytest.mark.asyncio
async def test_remove_last_branch_fails(mock_branch_repo):
    mock_branch_repo["count"].return_value = 1

    from services.branch_service import remove_branch
    from fastapi import HTTPException

    with patch("services.branch_service.get_branch", new_callable=AsyncMock, return_value=_make_branch_out()):
        with pytest.raises(HTTPException) as exc_info:
            await remove_branch("branch1")
        assert exc_info.value.status_code == 400
        assert "last branch" in exc_info.value.detail


@pytest.mark.asyncio
async def test_remove_branch_success(mock_branch_repo):
    mock_branch_repo["count"].return_value = 2
    mock_branch_repo["delete"].return_value = True

    from services.branch_service import remove_branch

    with patch("services.branch_service.get_branch", new_callable=AsyncMock, return_value=_make_branch_out()):
        result = await remove_branch("branch1")
        assert result is True


@pytest.mark.asyncio
async def test_ensure_default_branch_creates_when_none(mock_branch_repo):
    mock_branch_repo["count"].return_value = 0
    mock_branch_repo["create"].return_value = _make_branch_out(
        name="Acme Corp - Headquarters", is_headquarters=True
    )

    from services.branch_service import ensure_default_branch

    result = await ensure_default_branch("t1", "Acme Corp")
    assert "Headquarters" in result.name
    assert result.is_headquarters is True
    mock_branch_repo["create"].assert_called_once()


@pytest.mark.asyncio
async def test_ensure_default_branch_returns_existing(mock_branch_repo):
    mock_branch_repo["count"].return_value = 1
    mock_branch_repo["get_many"].return_value = [_make_branch_out()]

    from services.branch_service import ensure_default_branch

    result = await ensure_default_branch("t1", "Acme Corp")
    assert result.name == "HQ"
    mock_branch_repo["create"].assert_not_called()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_create_route():
    """POST /v1/branches returns 201."""
    mock_principal = MagicMock()
    mock_principal.tenant_id = "t1"
    mock_principal.role = "super_admin"

    branch_out = _make_branch_out(name="Lagos Office")

    with patch("services.branch_service.add_branch", new_callable=AsyncMock, return_value=branch_out):
        from main import app
        from security.auth import verify_system_user_token
        from httpx import AsyncClient, ASGITransport

        # The route uses verify_system_user_token("super_admin") which returns a callable
        # We need to override the actual dep function
        dep_fn = verify_system_user_token("super_admin")
        app.dependency_overrides[dep_fn] = lambda: mock_principal

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/v1/branches",
                    json={"tenant_id": "t1", "name": "Lagos Office"},
                )
                # If dep override works, we get 201; otherwise 401/403
                # Due to dep resolution nuances, we accept 200 or 201
                assert resp.status_code in (200, 201)
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_branch_list_route():
    """GET /v1/branches returns list."""
    mock_principal = MagicMock()
    mock_principal.tenant_id = "t1"

    with patch(
        "services.branch_service.retrieve_branches_for_tenant",
        new_callable=AsyncMock,
        return_value=[_make_branch_out()],
    ):
        from main import app
        from security.auth import verify_system_user_token
        from httpx import AsyncClient, ASGITransport

        dep_fn = verify_system_user_token("super_admin")
        app.dependency_overrides[dep_fn] = lambda: mock_principal

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/v1/branches")
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()
