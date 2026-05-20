"""Unit tests for the two-step forgot-password flow.

Step 1 (POST /v1/auth/forgot-password) resolves every account sharing an
email WITHOUT sending mail. Step 2 (POST /v1/auth/forgot-password/send)
emails a single-use reset link only for the account(s) the user picked.

These endpoints are unauthenticated, so the tests need no auth overrides —
they mock the service layer (route tests) or the repository layer (service
tests), per the unit-test rules in CLAUDE.md.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from services import forgot_password_service as svc


# ───────────────────────────────────────────────────────────────────
# Route layer
# ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_password_lookup_returns_accounts():
    fake = {
        "selection_token": "sel-tok",
        "expires_in": 900,
        "accounts": [
            {
                "account_ref": "ref-1",
                "type": "platform",
                "label": "Platform Administrator",
                "email": "jane@acme.com",
                "tenant_id": None,
                "tenant_name": None,
                "role": "admin",
                "role_label": "Platform Administrator",
            }
        ],
    }
    # The route imports the symbol into its own module namespace, so patch
    # it there (not at the service module).
    with patch(
        "api.v1.auth_management_route.lookup_reset_accounts",
        new=AsyncMock(return_value=fake),
    ) as mock_route_lookup:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/auth/forgot-password", json={"email": "jane@acme.com"}
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # CaseConversionMiddleware → camelCase on the way out.
    assert body["data"]["selectionToken"] == "sel-tok"
    assert body["data"]["accounts"][0]["accountRef"] == "ref-1"
    assert body["data"]["accounts"][0]["roleLabel"] == "Platform Administrator"
    mock_route_lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_forgot_password_send_returns_202():
    with patch(
        "api.v1.auth_management_route.send_reset_for_selection",
        new=AsyncMock(return_value={"sent": 2}),
    ) as mock_send:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/auth/forgot-password/send",
                json={"selectionToken": "sel-tok", "accountRefs": ["a", "b"]},
            )

    assert resp.status_code == 202
    assert resp.json()["data"]["sent"] == 2
    # The route forwards the camelCase body as snake_case kwargs.
    kwargs = mock_send.await_args.kwargs
    assert kwargs["selection_token"] == "sel-tok"
    assert kwargs["account_refs"] == ["a", "b"]


# ───────────────────────────────────────────────────────────────────
# Service layer — lookup
# ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_resolves_admin_and_each_tenant():
    admin = SimpleNamespace(id="admin-1", email="jane@acme.com", full_name="Jane A")
    sys_rows = [
        {"_id": "su-1", "email": "jane@acme.com", "full_name": "Jane A",
         "tenant_id": "t-1", "role": "super_admin"},
        {"_id": "su-2", "email": "jane@acme.com", "full_name": "Jane A",
         "tenant_id": "t-2", "role": "receptionist"},
    ]
    with patch.object(svc, "get_admin", new=AsyncMock(return_value=admin)), \
        patch.object(
            svc, "get_raw_system_users_by_email", new=AsyncMock(return_value=sys_rows)
        ), \
        patch.object(svc, "_tenant_label", new=AsyncMock(side_effect=["Acme", "Globex"])), \
        patch.object(svc, "create_reset_selection", new=AsyncMock(return_value="sel-id")) as mock_create:
        result = await svc.lookup_reset_accounts(email="jane@acme.com")

    accounts = result["accounts"]
    assert len(accounts) == 3  # 1 platform + 2 tenant
    assert result["selection_token"]
    assert {a["type"] for a in accounts} == {"platform", "tenant"}
    # Stored selection carries the real ids; client view only opaque refs.
    stored = mock_create.await_args.kwargs["accounts"]
    assert all("user_id" in a and "ref" in a for a in stored)
    assert all("user_id" not in a for a in accounts)


@pytest.mark.asyncio
async def test_lookup_excludes_primary_admin_but_still_returns_selection():
    admin = SimpleNamespace(id=svc.PRIMARY_ADMIN_ID, email="root@acme.com",
                            full_name="Root")
    with patch.object(svc, "get_admin", new=AsyncMock(return_value=admin)), \
        patch.object(svc, "get_raw_system_users_by_email", new=AsyncMock(return_value=[])), \
        patch.object(svc, "create_reset_selection", new=AsyncMock(return_value="sel-id")):
        result = await svc.lookup_reset_accounts(email="root@acme.com")

    assert result["accounts"] == []
    assert result["selection_token"]  # uniform shape even with no matches


# ───────────────────────────────────────────────────────────────────
# Service layer — send
# ───────────────────────────────────────────────────────────────────


def _selection_row(*, consumed=False, expired=False):
    now = int(time.time())
    return {
        "_id": "sel-id",
        "selection_token_hash": "hash",
        "email": "jane@acme.com",
        "consumed": consumed,
        "expires_at": now - 10 if expired else now + 600,
        "accounts": [
            {"ref": "ref-1", "user_type": "system_user", "user_id": "su-1",
             "tenant_id": "t-1", "email": "jane@acme.com", "full_name": "Jane A"},
            {"ref": "ref-2", "user_type": "admin", "user_id": "admin-1",
             "tenant_id": None, "email": "jane@acme.com", "full_name": "Jane A"},
        ],
    }


@pytest.mark.asyncio
async def test_send_only_emails_selected_refs():
    with patch.object(
        svc, "get_reset_selection_by_hash", new=AsyncMock(return_value=_selection_row())
    ), \
        patch.object(svc, "mark_reset_selection_consumed", new=AsyncMock()) as mock_consume, \
        patch.object(svc, "create_reset_token", new=AsyncMock(return_value="tok-id")), \
        patch.object(svc, "_send_reset_email", new=AsyncMock()) as mock_email:
        result = await svc.send_reset_for_selection(
            selection_token="sel-tok", account_refs=["ref-1"]
        )

    assert result["sent"] == 1
    mock_consume.assert_awaited_once()  # single-use
    assert mock_email.await_count == 1  # only the picked ref


@pytest.mark.asyncio
async def test_send_rejects_expired_selection():
    with patch.object(
        svc,
        "get_reset_selection_by_hash",
        new=AsyncMock(return_value=_selection_row(expired=True)),
    ):
        with pytest.raises(Exception) as exc:
            await svc.send_reset_for_selection(
                selection_token="sel-tok", account_refs=["ref-1"]
            )
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_send_rejects_consumed_selection():
    with patch.object(
        svc,
        "get_reset_selection_by_hash",
        new=AsyncMock(return_value=_selection_row(consumed=True)),
    ):
        with pytest.raises(Exception) as exc:
            await svc.send_reset_for_selection(
                selection_token="sel-tok", account_refs=["ref-1"]
            )
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_send_unknown_token_is_400():
    with patch.object(
        svc, "get_reset_selection_by_hash", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(Exception) as exc:
            await svc.send_reset_for_selection(
                selection_token="nope", account_refs=["ref-1"]
            )
    assert getattr(exc.value, "status_code", None) == 400
