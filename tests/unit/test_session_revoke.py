"""Unit tests for the session-revoke flow.

These guard the property that revoking a session also kills the auth
tokens that back it. Earlier the revoke only deleted the ``sessions``
row, leaving the device's access + refresh tokens valid until natural
expiry — i.e. the UI told the user they had logged the device out, but
the device kept making authenticated requests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import session_service


_SESSION_ID = "64f1a2b3c4d5e6f7a8b9c0d1"
_ACCESS_TOKEN_ID = "650000000000000000000abc"


def _session_stub(access_token_id: str = _ACCESS_TOKEN_ID) -> MagicMock:
    sess = MagicMock()
    sess.access_token_id = access_token_id
    return sess


@pytest.mark.asyncio
async def test_revoke_session_drops_access_and_refresh_tokens() -> None:
    delete_one_result = MagicMock(deleted_count=1)
    with (
        patch.object(session_service, "get_session", new=AsyncMock(return_value=_session_stub())),
        patch.object(session_service, "delete_session", new=AsyncMock(return_value=delete_one_result)) as mock_delete_session,
        patch.object(session_service, "delete_access_token", new=AsyncMock()) as mock_delete_access,
        patch.object(
            session_service,
            "delete_refresh_tokens_by_previous_access_token",
            new=AsyncMock(return_value=1),
        ) as mock_delete_refresh,
    ):
        await session_service.revoke_session(
            session_id=_SESSION_ID, user_id="u1", user_type="admin"
        )

    mock_delete_refresh.assert_awaited_once_with(_ACCESS_TOKEN_ID)
    mock_delete_access.assert_awaited_once_with(_ACCESS_TOKEN_ID)
    mock_delete_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_session_404_when_missing() -> None:
    from fastapi import HTTPException

    with patch.object(session_service, "get_session", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await session_service.revoke_session(
                session_id=_SESSION_ID, user_id="u1", user_type="admin"
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_revoke_all_except_current_kills_tokens_in_bulk() -> None:
    targets = [
        _session_stub(access_token_id="at_one"),
        _session_stub(access_token_id="at_two"),
        _session_stub(access_token_id=""),  # missing id is skipped, not crashed
    ]
    with (
        patch.object(session_service, "get_sessions", new=AsyncMock(return_value=targets)),
        patch.object(
            session_service,
            "delete_refresh_tokens_by_previous_access_token",
            new=AsyncMock(return_value=1),
        ) as mock_delete_refresh,
        patch.object(
            session_service,
            "delete_access_tokens_by_ids",
            new=AsyncMock(return_value=2),
        ) as mock_delete_access_bulk,
        patch.object(session_service, "delete_sessions", new=AsyncMock(return_value=2)) as mock_delete_sessions,
    ):
        result = await session_service.revoke_all_sessions_except_current(
            user_id="u1", user_type="admin", current_token_id="at_current"
        )

    assert result == 2
    # One refresh-chain delete per non-empty access token id.
    assert mock_delete_refresh.await_count == 2
    mock_delete_refresh.assert_any_await("at_one")
    mock_delete_refresh.assert_any_await("at_two")
    mock_delete_access_bulk.assert_awaited_once_with(["at_one", "at_two"])
    mock_delete_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_all_except_current_noop_when_no_other_sessions() -> None:
    with (
        patch.object(session_service, "get_sessions", new=AsyncMock(return_value=[])),
        patch.object(
            session_service,
            "delete_refresh_tokens_by_previous_access_token",
            new=AsyncMock(),
        ) as mock_delete_refresh,
        patch.object(
            session_service,
            "delete_access_tokens_by_ids",
            new=AsyncMock(),
        ) as mock_delete_access_bulk,
        patch.object(session_service, "delete_sessions", new=AsyncMock(return_value=0)),
    ):
        result = await session_service.revoke_all_sessions_except_current(
            user_id="u1", user_type="admin", current_token_id="at_current"
        )

    assert result == 0
    mock_delete_refresh.assert_not_awaited()
    mock_delete_access_bulk.assert_not_awaited()
