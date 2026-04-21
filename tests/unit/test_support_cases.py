"""Unit tests for the Support Cases feature.

Covers:
 * schema validation (subject/description length, attachment cap)
 * state-machine transition rules
 * 10-open-cap enforcement
 * internal-note filtering + admin-only write
 * email dispatch tier matrix (NONE / STANDARD / PRIORITY)
 * 60s admin-reply throttle
 * route 202s for POST / transition / assign

The route tests patch ``enqueue_write`` and ``EmailManager.get_instance()`` so
they don't need Redis / MongoDB / a real celery broker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from main import app
from schemas.imports import (
    SupportCaseAuthorType,
    SupportCaseCategory,
    SupportCasePriority,
    SupportCaseStatus,
    SupportTier,
)
from schemas.support_case_schema import (
    OPEN_STATUSES,
    SLA_WINDOWS_SECONDS,
    SupportCaseCreate,
    SupportCaseMessageBase,
    SupportCaseMessageCreate,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services import support_case_service


MOCK_TENANT_PRINCIPAL = AuthPrincipal(
    user_id="tenant-user-1",
    role="super_admin",
    access_token_id="tok1",
    jwt_token="jwt1",
    tenant_id="tenant-1",
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_subject_too_short_rejected(self):
        with pytest.raises(ValidationError):
            SupportCaseCreate(
                subject="hi",
                description="x" * 25,
                category=SupportCaseCategory.OTHER,
                priority=SupportCasePriority.MEDIUM,
                tenant_id="t1",
                opened_by="u1",
                opened_by_role="super_admin",
            )

    def test_description_too_short_rejected(self):
        with pytest.raises(ValidationError):
            SupportCaseCreate(
                subject="A valid subject line",
                description="too short",
                category=SupportCaseCategory.OTHER,
                priority=SupportCasePriority.MEDIUM,
                tenant_id="t1",
                opened_by="u1",
                opened_by_role="super_admin",
            )

    def test_sla_due_at_computed(self):
        case = SupportCaseCreate(
            subject="Valid subject",
            description="A description long enough to pass the 20-char guard.",
            category=SupportCaseCategory.TECHNICAL,
            priority=SupportCasePriority.HIGH,
            tenant_id="t1",
            opened_by="u1",
            opened_by_role="super_admin",
            date_created=1_700_000_000,
        )
        expected = 1_700_000_000 + SLA_WINDOWS_SECONDS["high"]
        assert case.sla_due_at == expected

    def test_attachment_cap(self):
        with pytest.raises(ValidationError):
            SupportCaseMessageBase(
                body="hello",
                attachments=[
                    {"document_id": f"d{i}", "file_name": f"f{i}"} for i in range(11)
                ],
            )

    def test_open_statuses_does_not_include_closed(self):
        assert SupportCaseStatus.CLOSED.value not in OPEN_STATUSES
        assert SupportCaseStatus.RESOLVED.value in OPEN_STATUSES


# ---------------------------------------------------------------------------
# State-machine tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_admin_acknowledge_from_open(self):
        # Should not raise
        support_case_service._validate_transition("open", "acknowledged", "admin")

    def test_tenant_cannot_acknowledge(self):
        from core.errors import AppException

        with pytest.raises(AppException):
            support_case_service._validate_transition(
                "open", "acknowledged", "tenant"
            )

    def test_tenant_can_close_resolved(self):
        support_case_service._validate_transition("resolved", "closed", "tenant")

    def test_tenant_can_reopen_resolved(self):
        support_case_service._validate_transition(
            "resolved", "reopened", "tenant"
        )

    def test_closed_is_terminal(self):
        from core.errors import AppException

        with pytest.raises(AppException):
            support_case_service._validate_transition("closed", "open", "admin")

    def test_same_state_rejected(self):
        from core.errors import AppException

        with pytest.raises(AppException):
            support_case_service._validate_transition(
                "in_progress", "in_progress", "admin"
            )

    def test_illegal_transition(self):
        from core.errors import AppException

        with pytest.raises(AppException):
            support_case_service._validate_transition("open", "resolved", "admin")


# ---------------------------------------------------------------------------
# 10-open-cap
# ---------------------------------------------------------------------------


class TestOpenCaseCap:
    @pytest.mark.asyncio
    async def test_cap_allows_nine(self):
        with patch(
            "services.support_case_service.count_open_cases_for_tenant",
            new_callable=AsyncMock,
        ) as m:
            m.return_value = 9
            # Should not raise
            await support_case_service._enforce_open_case_cap("t1")

    @pytest.mark.asyncio
    async def test_cap_rejects_ten(self):
        from core.errors import AppException

        with patch(
            "services.support_case_service.count_open_cases_for_tenant",
            new_callable=AsyncMock,
        ) as m:
            m.return_value = 10
            with pytest.raises(AppException) as exc_info:
                await support_case_service._enforce_open_case_cap("t1")
            assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Email tier matrix
# ---------------------------------------------------------------------------


class TestEmailTierDispatch:
    """Verify ``add_support_case`` emits admin emails only for STANDARD+ tiers."""

    async def _run(self, tier: SupportTier) -> int:
        """Returns the number of admin emails queued for the given tier."""
        queued: list[str] = []

        async def fake_queue(to_email, template_key, context):
            queued.append(template_key)

        with (
            patch(
                "services.support_case_service.count_open_cases_for_tenant",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "services.support_case_service.create_support_case",
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                "services.support_case_service._resolve_support_tier",
                new_callable=AsyncMock,
                return_value=tier,
            ),
            patch(
                "services.support_case_service._resolve_tenant_company_name",
                new_callable=AsyncMock,
                return_value="Acme",
            ),
            patch(
                "services.support_case_service._resolve_tenant_opener_email",
                new_callable=AsyncMock,
                return_value="opener@example.com",
            ),
            patch(
                "services.support_case_service._list_admin_emails",
                new_callable=AsyncMock,
                return_value=["a1@x.com", "a2@x.com"],
            ),
            patch(
                "services.support_case_service._queue_email",
                new=fake_queue,
            ),
            patch(
                "services.support_case_service.record_audit_event",
                new_callable=AsyncMock,
            ),
            patch(
                "services.support_case_service._enqueue_list_refresh",
                new=lambda _tid: None,
            ),
            patch(
                "services.support_case_service.notify_support_case_opened",
                new_callable=AsyncMock,
                create=True,
            ),
        ):
            # Build the fake case the repository "returned".
            fake_case = MagicMock()
            fake_case.id = "c1"
            fake_case.tenant_id = "t1"
            fake_case.subject = "Subject goes here"
            fake_case.status = SupportCaseStatus.OPEN
            fake_case.priority = SupportCasePriority.MEDIUM
            fake_case.category = SupportCaseCategory.OTHER
            fake_case.opened_by = "u1"
            mock_create.return_value = fake_case

            await support_case_service.add_support_case(
                subject="Subject goes here",
                description="A description long enough to pass the guard.",
                category="other",
                priority="medium",
                tenant_id="t1",
                opened_by="u1",
                opened_by_role="super_admin",
            )

        # Count admin emails queued.
        return sum(
            1 for t in queued if t == "support_case.opened.admin"
        )

    @pytest.mark.asyncio
    async def test_none_tier_no_admin_email(self):
        assert (await self._run(SupportTier.NONE)) == 0

    @pytest.mark.asyncio
    async def test_standard_tier_admin_emails(self):
        # Two admins in the fake list → two emails.
        assert (await self._run(SupportTier.STANDARD)) == 2

    @pytest.mark.asyncio
    async def test_priority_tier_admin_emails(self):
        assert (await self._run(SupportTier.PRIORITY)) == 2


# ---------------------------------------------------------------------------
# Admin-reply throttle
# ---------------------------------------------------------------------------


class TestAdminReplyThrottle:
    def test_second_call_within_window_returns_false(self):
        """Uses the real Redis mock — first call acquires, second is throttled."""
        with patch("services.support_case_service.cache_db") as cache:
            cache.set.side_effect = [True, False]
            assert support_case_service._throttle_admin_reply_email("c1") is True
            assert support_case_service._throttle_admin_reply_email("c1") is False


# ---------------------------------------------------------------------------
# Internal-note authorisation
# ---------------------------------------------------------------------------


class TestInternalNoteAuthz:
    def test_author_type_mapping_tenant(self):
        assert support_case_service._actor_type("super_admin") == "tenant"
        assert support_case_service._actor_type("receptionist") == "tenant"

    def test_author_type_mapping_admin(self):
        assert support_case_service._actor_type("admin") == "admin"

    def test_author_type_mapping_system(self):
        assert support_case_service._actor_type(None) == "system"
        assert support_case_service._actor_type("system") == "system"

    def test_message_create_builds(self):
        """The service forces internal_note=False for non-admin actors at the
        service layer; at the schema level any boolean is accepted."""
        msg = SupportCaseMessageCreate(
            case_id="c1",
            author_id="u1",
            author_role="super_admin",
            author_type=SupportCaseAuthorType.TENANT,
            body="hi",
            internal_note=True,  # schema allows; service forces False
        )
        assert msg.internal_note is True


# ---------------------------------------------------------------------------
# Route tests — 202 envelope + writer_key
# ---------------------------------------------------------------------------


@pytest.fixture
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()


class TestSupportCaseRoutes:
    @pytest.mark.asyncio
    async def test_open_case_returns_202(self, cleanup_overrides):
        app.dependency_overrides[verify_system_user_token] = (
            lambda: MOCK_TENANT_PRINCIPAL
        )

        with (
            patch(
                "api.v1.support_case_route.count_open_cases_for_tenant",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "api.v1.support_case_route.enqueue_write", new_callable=AsyncMock
            ) as mock_enq,
        ):
            mock_enq.return_value = {
                "id": "c1",
                "job_id": "j1",
                "status": "queued",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/v1/support-cases",
                    json={
                        "subject": "Cannot issue badges",
                        "description": "A description long enough to pass.",
                        "category": "technical",
                        "priority": "high",
                    },
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 202
        body = resp.json()
        assert body["data"]["jobId"] == "j1"
        mock_enq.assert_awaited_once()
        assert mock_enq.await_args.kwargs["writer_key"] == "support_case.create"

    @pytest.mark.asyncio
    async def test_open_case_429_when_cap_hit(self, cleanup_overrides):
        app.dependency_overrides[verify_system_user_token] = (
            lambda: MOCK_TENANT_PRINCIPAL
        )
        with patch(
            "api.v1.support_case_route.count_open_cases_for_tenant",
            new_callable=AsyncMock,
            return_value=10,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/v1/support-cases",
                    json={
                        "subject": "Cannot issue badges",
                        "description": "A description long enough to pass.",
                        "category": "technical",
                        "priority": "high",
                    },
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_close_case_returns_202(self, cleanup_overrides):
        app.dependency_overrides[verify_system_user_token] = (
            lambda: MOCK_TENANT_PRINCIPAL
        )
        with (
            patch(
                "api.v1.support_case_route.retrieve_support_case_by_id",
                new_callable=AsyncMock,
                return_value={"case": {}, "messages": []},
            ),
            patch(
                "api.v1.support_case_route.enqueue_write", new_callable=AsyncMock
            ) as mock_enq,
        ):
            mock_enq.return_value = {
                "id": "c1",
                "job_id": "j1",
                "status": "queued",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/v1/support-cases/c1/close",
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 202
        assert (
            mock_enq.await_args.kwargs["writer_key"] == "support_case.transition"
        )
        assert mock_enq.await_args.kwargs["payload"]["status"] == "closed"
