"""Unit tests for the Tenant Agreements subsystem.

Covers the fixed-allowlist templating engine, the per-tenant build/accept/
decline service, the version-gated status/gate logic, and the soft-gate helper
in security.auth. Everything is mocked at the import site — nothing touches a
live database or the Redis cache.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _para(text: str) -> dict:
    return {
        "id": "seed",
        "type": "paragraph",
        "props": {},
        "content": [{"type": "text", "text": text, "styles": {}}],
        "children": [],
    }


def _flatten(blocks) -> str:
    return "\n".join(
        "".join(n.get("text", "") for n in b.get("content", [])) for b in blocks
    )


# ---------------------------------------------------------------------------
# templating
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_templating_canonical_legacy_and_unknown():
    from services.tenant_agreements import templating as t

    blocks = [
        _para("Name: [company_name]"),
        # legacy verbose alias (curly apostrophe) -> organization_address
        _para("Address: [Insert Organization’s representative address]"),
        _para("Keep [some_unknown_token] literal"),
    ]
    out = t.resolve_blocks(
        blocks,
        {"company_name": "Acme Ltd", "organization_address": "1 Main St, Lagos"},
    )
    text = _flatten(out)
    assert "Acme Ltd" in text
    assert "1 Main St, Lagos" in text
    assert "[Insert Organization" not in text  # legacy alias substituted
    assert "[some_unknown_token]" in text  # unknown bracket left literal
    # Block ids are regenerated (no template ids leak through).
    assert all(b["id"].startswith("b_") for b in out)


@pytest.mark.unit
def test_templating_unset_allowlisted_token_falls_back():
    from services.tenant_agreements import templating as t

    out = t.resolve_blocks([_para("Contact: [contact_email]")], {})
    assert "[To be provided]" in _flatten(out)


@pytest.mark.unit
def test_templating_case_and_space_insensitive():
    from services.tenant_agreements import templating as t

    out = t.substitute_text("Hi [Company Name]", {"company_name": "Acme"})
    assert out == "Hi Acme"


# ---------------------------------------------------------------------------
# service: build / accept / decline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retrieve_or_build_creates_when_missing():
    import services.tenant_agreement_service as svc

    body = [_para("Name: [company_name]")]
    with (
        patch.object(svc.repo, "get_for_tenant", AsyncMock(return_value=None)),
        patch.object(
            svc.master,
            "get_master_for_build",
            AsyncMock(return_value=(body, 1, "DPA", "summary")),
        ),
        patch.object(
            svc,
            "_build_values_for_tenant",
            AsyncMock(return_value={"company_name": "Acme"}),
        ),
        patch.object(
            svc.repo,
            "upsert",
            AsyncMock(return_value=SimpleNamespace(id="a1", accepted=False, version=1)),
        ) as up,
    ):
        res = await svc.retrieve_or_build("t1", "dpa")

    assert res is not None and res.id == "a1"
    up.assert_awaited_once()
    created = up.await_args.args[0]
    assert created.version == 1
    assert created.accepted is False
    assert "Acme" in _flatten(created.body)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retrieve_returns_frozen_when_accepted_at_current_version():
    import services.tenant_agreement_service as svc

    existing = SimpleNamespace(accepted=True, version=2, id="a1")
    with (
        patch.object(svc.repo, "get_for_tenant", AsyncMock(return_value=existing)),
        patch.object(
            svc.master,
            "get_master_for_build",
            AsyncMock(return_value=([_para("x")], 2, "DPA", None)),
        ),
        patch.object(svc.repo, "upsert", AsyncMock()) as up,
    ):
        res = await svc.retrieve_or_build("t1", "dpa")

    assert res is existing
    up.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retrieve_rebuilds_when_master_version_bumped():
    import services.tenant_agreement_service as svc

    # Accepted at v1 but the master is now v2 -> must rebuild as unaccepted.
    existing = SimpleNamespace(accepted=True, version=1, id="a1", created_at=10)
    with (
        patch.object(svc.repo, "get_for_tenant", AsyncMock(return_value=existing)),
        patch.object(
            svc.master,
            "get_master_for_build",
            AsyncMock(return_value=([_para("[company_name]")], 2, "DPA", None)),
        ),
        patch.object(
            svc,
            "_build_values_for_tenant",
            AsyncMock(return_value={"company_name": "A"}),
        ),
        patch.object(
            svc.repo,
            "upsert",
            AsyncMock(return_value=SimpleNamespace(id="a1", accepted=False, version=2)),
        ) as up,
    ):
        res = await svc.retrieve_or_build("t1", "dpa")

    up.assert_awaited_once()
    assert up.await_args.args[0].version == 2
    assert up.await_args.args[0].accepted is False
    assert res.version == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mark_accepted_freezes_stamps_and_invalidates():
    import services.tenant_agreement_service as svc

    unaccepted = SimpleNamespace(accepted=False, id="a1", version=1)
    with (
        patch.object(svc, "retrieve_or_build", AsyncMock(return_value=unaccepted)),
        patch.object(
            svc.repo,
            "update",
            AsyncMock(return_value=SimpleNamespace(id="a1", version=1)),
        ) as up,
        patch.object(svc, "invalidate_state") as inval,
        patch.object(svc, "_audit", AsyncMock()),
    ):
        res = await svc.mark_accepted("t1", "dpa", actor_id="u1")

    assert res is not None
    up.assert_awaited_once()
    passed = up.await_args.args[2]
    assert passed.accepted is True
    assert passed.accepted_by == "u1"
    inval.assert_called_once_with("t1")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mark_accepted_noop_without_master():
    import services.tenant_agreement_service as svc

    with (
        patch.object(svc, "retrieve_or_build", AsyncMock(return_value=None)),
        patch.object(svc.repo, "update", AsyncMock()) as up,
    ):
        res = await svc.mark_accepted("t1", "dpa", actor_id="u1")

    assert res is None
    up.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mark_declined_stamps_declined_at():
    import services.tenant_agreement_service as svc

    current = SimpleNamespace(accepted=False, id="a1", version=1)
    with (
        patch.object(svc, "retrieve_or_build", AsyncMock(return_value=current)),
        patch.object(
            svc.repo,
            "update",
            AsyncMock(return_value=SimpleNamespace(id="a1", version=1)),
        ) as up,
        patch.object(svc, "invalidate_state"),
        patch.object(svc, "_audit", AsyncMock()),
    ):
        await svc.mark_declined("t1", "dpa", actor_id="u1")

    up.assert_awaited_once()
    assert up.await_args.args[2].declined_at is not None


# ---------------------------------------------------------------------------
# status + gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_status_unpublished_master_is_not_required():
    import services.tenant_agreement_service as svc

    with (
        patch.object(
            svc.master, "current_master_version", AsyncMock(return_value=None)
        ),
        patch.object(svc.repo, "get_for_tenant", AsyncMock()) as gf,
    ):
        ok, pending = await svc.agreements_status("t1")

    assert ok is True and pending == []
    gf.assert_not_awaited()  # never even checks the tenant row


@pytest.mark.asyncio
@pytest.mark.unit
async def test_status_pending_when_not_accepted():
    import services.tenant_agreement_service as svc

    async def _ver(slug):
        return 1 if slug.endswith("data-processing-agreement") else None

    with (
        patch.object(svc.master, "current_master_version", side_effect=_ver),
        patch.object(svc.repo, "get_for_tenant", AsyncMock(return_value=None)),
    ):
        ok, pending = await svc.agreements_status("t1")

    assert ok is False
    assert pending == ["dpa"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_status_ok_when_accepted_at_current_version():
    import services.tenant_agreement_service as svc

    async def _ver(slug):
        return 1 if slug.endswith("data-processing-agreement") else None

    with (
        patch.object(svc.master, "current_master_version", side_effect=_ver),
        patch.object(
            svc.repo,
            "get_for_tenant",
            AsyncMock(return_value=SimpleNamespace(accepted=True, version=1)),
        ),
    ):
        ok, pending = await svc.agreements_status("t1")

    assert ok is True and pending == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gate_check_short_circuits_on_cached_ok():
    import services.tenant_agreement_service as svc

    with (
        patch.object(svc.cache_db, "get", return_value="ok"),
        patch.object(svc, "agreements_status", AsyncMock()) as status,
    ):
        ok, pending = await svc.agreements_gate_check("t1")

    assert ok is True and pending == []
    status.assert_not_awaited()  # cache hit never recomputes


# ---------------------------------------------------------------------------
# security.auth soft gate
# ---------------------------------------------------------------------------


def _req(method: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(
        method=method,
        scope={"route": SimpleNamespace(path=path)},
        url=SimpleNamespace(path=path),
    )


_PRINCIPAL = SimpleNamespace(tenant_id="t1", role="receptionist", user_id="u1")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/agreements/dpa/accept"),  # accepting is never gated
        ("GET", "/v1/visitors"),  # reads are never gated
        ("GET", "/v1/appointments"),
        ("POST", "/v1/user-settings"),  # basic self-service write
        ("POST", "/v1/auth/change-password"),
        ("GET", "/v1/dashboard/stats"),
    ],
)
async def test_gate_allows_non_operational_requests(method, path):
    from security import auth

    # If the service were consulted it would block; it must NOT be consulted.
    with patch(
        "services.tenant_agreement_service.agreements_gate_check",
        AsyncMock(return_value=(False, ["dpa"])),
    ) as check:
        await auth._enforce_agreement_acceptance(_req(method, path), _PRINCIPAL)
    check.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/v1/appointments",
        "/v1/visitors",
        "/v1/checkin-configs/{config_id}/submit",
        "/v1/checkins/{checkin_id}/approve",
        "/v1/badges",
    ],
)
async def test_gate_blocks_operational_writes_when_pending(path):
    from core.errors import AppException
    from security import auth

    with patch(
        "services.tenant_agreement_service.agreements_gate_check",
        AsyncMock(return_value=(False, ["dpa"])),
    ):
        with pytest.raises(AppException) as exc:
            await auth._enforce_agreement_acceptance(_req("POST", path), _PRINCIPAL)

    assert exc.value.status_code == 403
    assert exc.value.detail["details"]["code"] == "AGREEMENT_ACCEPTANCE_REQUIRED"
    assert exc.value.detail["details"]["pending"] == ["dpa"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gate_fails_open_on_error():
    from security import auth

    with patch(
        "services.tenant_agreement_service.agreements_gate_check",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        # Must not raise — a backend blip never locks a tenant out.
        await auth._enforce_agreement_acceptance(
            _req("POST", "/v1/visitors"), _PRINCIPAL
        )


# ---------------------------------------------------------------------------
# acceptance API (route layer)
# ---------------------------------------------------------------------------


@pytest.fixture
def _cleanup_overrides():
    from main import app

    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accept_endpoint_returns_200_synchronously(_cleanup_overrides):
    from httpx import ASGITransport, AsyncClient

    from main import app
    from schemas.tenant_agreement_schema import TenantAgreementOut
    from security.auth import verify_super_admin_token
    from security.principal import AuthPrincipal

    principal = AuthPrincipal(
        user_id="u1",
        role="super_admin",
        access_token_id="t",
        jwt_token="j",
        tenant_id="tenant-001",
    )
    app.dependency_overrides[verify_super_admin_token] = lambda: principal

    accepted = TenantAgreementOut(
        _id="a1",
        tenant_id="tenant-001",
        agreement_key="dpa",
        version=1,
        title="Data Processing Agreement",
        accepted=True,
        accepted_at=123,
        accepted_by="u1",
    )
    with patch(
        "api.v1.tenant_agreement_route.mark_accepted",
        new_callable=AsyncMock,
        return_value=accepted,
    ) as mock_accept:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/agreements/dpa/accept",
                headers={"Authorization": "Bearer token"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["accepted"] is True
    assert body["data"]["agreementKey"] == "dpa"
    assert mock_accept.await_args.args[1] == "dpa"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accept_unknown_key_404(_cleanup_overrides):
    from httpx import ASGITransport, AsyncClient

    from main import app
    from security.auth import verify_super_admin_token
    from security.principal import AuthPrincipal

    principal = AuthPrincipal(
        user_id="u1",
        role="super_admin",
        access_token_id="t",
        jwt_token="j",
        tenant_id="tenant-001",
    )
    app.dependency_overrides[verify_super_admin_token] = lambda: principal

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/agreements/not-a-real-key/accept",
            headers={"Authorization": "Bearer token"},
        )

    assert resp.status_code == 404
