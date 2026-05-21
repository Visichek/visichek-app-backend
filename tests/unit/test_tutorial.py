"""Unit tests for the tutorial-progress feature (schema → repo → service → route).

Route tests use httpx AsyncClient with ASGITransport and override
``verify_any_token`` (the route's only auth dep) so no live token lookup runs.
Service tests mock the repository functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from schemas.imports import TutorialStatus, TutorialType, UserType
from schemas.tutorial_schema import TutorialOut
from security.auth import verify_any_token
from security.principal import AuthPrincipal

pytestmark = pytest.mark.asyncio


def _sample(**overrides) -> TutorialOut:
    base = TutorialOut(
        _id="650000000000000000000a01",
        user_id="u1",
        user_role="receptionist",
        user_type=UserType.SYSTEM_USER,
        tutorial_type=TutorialType.VISITOR_WORKFLOW,
        tutorial_status=TutorialStatus.IN_PROGRESS,
        version=1,
        tenant_id="t1",
    )
    return base.model_copy(update=overrides) if overrides else base


# ── Service layer ────────────────────────────────────────────────────


class TestTutorialService:
    @patch("services.tutorial_service.create_tutorial", new_callable=AsyncMock)
    @patch("services.tutorial_service.get_tutorial", new_callable=AsyncMock)
    async def test_record_creates_when_absent(self, mock_get, mock_create):
        from services.tutorial_service import record_tutorial_progress

        mock_get.return_value = None
        mock_create.return_value = _sample()

        result = await record_tutorial_progress(
            user_id="u1",
            user_role="receptionist",
            user_type=UserType.SYSTEM_USER,
            tutorial_type=TutorialType.VISITOR_WORKFLOW,
            tutorial_status=TutorialStatus.IN_PROGRESS,
        )

        mock_create.assert_awaited_once()
        assert result.tutorial_type == TutorialType.VISITOR_WORKFLOW

    async def test_tenant_user_cannot_record_platform_tutorial(self):
        from core.errors import AppException
        from services.tutorial_service import record_tutorial_progress

        with pytest.raises(AppException) as exc:
            await record_tutorial_progress(
                user_id="u1",
                user_role="receptionist",
                user_type=UserType.SYSTEM_USER,
                tutorial_type=TutorialType.ADMIN_DASHBOARD_OVERVIEW,
                tutorial_status=TutorialStatus.IN_PROGRESS,
            )
        assert exc.value.status_code == 403

    async def test_admin_cannot_record_tenant_tutorial(self):
        from core.errors import AppException
        from services.tutorial_service import record_tutorial_progress

        with pytest.raises(AppException) as exc:
            await record_tutorial_progress(
                user_id="a1",
                user_role="admin",
                user_type=UserType.ADMIN,
                tutorial_type=TutorialType.VISITOR_WORKFLOW,
                tutorial_status=TutorialStatus.IN_PROGRESS,
            )
        assert exc.value.status_code == 403

    @patch("services.tutorial_service.create_tutorial", new_callable=AsyncMock)
    @patch("services.tutorial_service.get_tutorial", new_callable=AsyncMock)
    async def test_cross_cutting_allowed_for_admin(self, mock_get, mock_create):
        from services.tutorial_service import record_tutorial_progress

        mock_get.return_value = None
        mock_create.return_value = _sample(
            user_role="admin",
            user_type=UserType.ADMIN,
            tutorial_type=TutorialType.GETTING_STARTED,
        )
        # An admin recording a cross-cutting tutorial must NOT be rejected.
        result = await record_tutorial_progress(
            user_id="a1",
            user_role="admin",
            user_type=UserType.ADMIN,
            tutorial_type=TutorialType.GETTING_STARTED,
            tutorial_status=TutorialStatus.IN_PROGRESS,
        )
        mock_create.assert_awaited_once()
        assert result.tutorial_type == TutorialType.GETTING_STARTED

    async def test_shell_sets_partition_the_enum(self):
        # Every TutorialType must be classified exactly once, or a new
        # tutorial silently falls into the tenant bucket / default-deny.
        from services.tutorial_service import (
            _CROSS_CUTTING_TUTORIALS,
            _PLATFORM_TUTORIALS,
            _TENANT_TUTORIALS,
        )

        assert (
            _PLATFORM_TUTORIALS | _CROSS_CUTTING_TUTORIALS | _TENANT_TUTORIALS
            == set(TutorialType)
        )
        assert _PLATFORM_TUTORIALS.isdisjoint(_CROSS_CUTTING_TUTORIALS)
        assert _PLATFORM_TUTORIALS.isdisjoint(_TENANT_TUTORIALS)
        assert _CROSS_CUTTING_TUTORIALS.isdisjoint(_TENANT_TUTORIALS)

    @patch("services.tutorial_service.update_tutorial", new_callable=AsyncMock)
    @patch("services.tutorial_service.get_tutorial", new_callable=AsyncMock)
    async def test_record_updates_when_present(self, mock_get, mock_update):
        from services.tutorial_service import record_tutorial_progress

        mock_get.return_value = _sample()
        mock_update.return_value = _sample(tutorial_status=TutorialStatus.COMPLETED)

        result = await record_tutorial_progress(
            user_id="u1",
            user_role="receptionist",
            user_type=UserType.SYSTEM_USER,
            tutorial_type=TutorialType.VISITOR_WORKFLOW,
            tutorial_status=TutorialStatus.COMPLETED,
        )

        mock_update.assert_awaited_once()
        # The upsert key must pin user_id + tutorial_type + version.
        filter_arg = mock_update.await_args.args[0]
        assert filter_arg == {
            "user_id": "u1",
            "tutorial_type": TutorialType.VISITOR_WORKFLOW,
            "version": 1,
        }
        assert result.tutorial_status == TutorialStatus.COMPLETED


# ── Route layer ──────────────────────────────────────────────────────


@pytest.fixture
async def client():
    # Admin principal → bypasses PlanEnforcementMiddleware; verify_any_token is
    # the route's only auth dep so this is all that's needed.
    app.dependency_overrides[verify_any_token] = lambda: AuthPrincipal(
        user_id="u1",
        role="receptionist",
        access_token_id="tok",
        jwt_token="tok",
        tenant_id="t1",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


class TestTutorialRoutes:
    @patch("api.v1.tutorial_route.retrieve_tutorial_progress", new_callable=AsyncMock)
    async def test_list_progress(self, mock_retrieve, client):
        mock_retrieve.return_value = [_sample()]

        resp = await client.get("/v1/tutorials")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        # CaseConversionMiddleware emits camelCase keys.
        assert body["data"][0]["tutorialType"] == "visitor_workflow"

    @patch("api.v1.tutorial_route.record_tutorial_progress", new_callable=AsyncMock)
    async def test_save_progress(self, mock_record, client):
        mock_record.return_value = _sample(tutorial_status=TutorialStatus.COMPLETED)

        resp = await client.put(
            "/v1/tutorials",
            json={
                "tutorialType": "visitor_workflow",
                "tutorialStatus": "completed",
                "version": 1,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Server derives identity from the token, not the body.
        kwargs = mock_record.await_args.kwargs
        assert kwargs["user_id"] == "u1"
        assert kwargs["user_type"] == UserType.SYSTEM_USER
        assert kwargs["tutorial_type"] == TutorialType.VISITOR_WORKFLOW

    async def test_route_rejects_cross_shell_tutorial(self, client):
        # The client fixture authenticates as a receptionist (tenant role);
        # the real service gate must refuse a platform tutorial with 403.
        resp = await client.put(
            "/v1/tutorials",
            json={
                "tutorialType": "admin_dashboard_overview",
                "tutorialStatus": "in_progress",
            },
        )
        assert resp.status_code == 403

    async def test_save_rejects_unknown_tutorial(self, client):
        resp = await client.put(
            "/v1/tutorials",
            json={"tutorialType": "not_a_real_tutorial", "tutorialStatus": "completed"},
        )
        assert resp.status_code == 422
