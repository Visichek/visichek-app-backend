"""Regression tests for the forced first-login password change.

The hole these pin: an admin-issued temporary password is never written to
``password_history`` at account creation, so the history check alone would
accept ``new_password == temp_password``. That cleared ``must_change_password``
while leaving the emailed cleartext valid forever — a forced password change
that changed nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from security.hash import hash_password
from services.password_change_service import _enforce_new_password_policy

TEMP_PASSWORD = "Tmp!Xy7q2Zra"
NEW_PASSWORD = "Fresh!Pw9Kdb"


@pytest.mark.asyncio
async def test_new_password_cannot_equal_current_password():
    """Reusing the temp password as the 'new' password must be rejected."""
    current_hash = hash_password(TEMP_PASSWORD)

    with patch(
        "services.password_change_service.check_password_history",
        new=AsyncMock(return_value=True),  # history is empty for a fresh account
    ):
        with pytest.raises(HTTPException) as exc:
            await _enforce_new_password_policy(
                "507f1f77bcf86cd799439011",
                TEMP_PASSWORD,
                role="system_user",
                current_hash=current_hash,
            )

    assert exc.value.status_code == 422
    assert "different from your current password" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_genuinely_new_password_is_accepted():
    """A different, policy-compliant password still goes through."""
    current_hash = hash_password(TEMP_PASSWORD)

    with patch(
        "services.password_change_service.check_password_history",
        new=AsyncMock(return_value=True),
    ):
        hashed = await _enforce_new_password_policy(
            "507f1f77bcf86cd799439011",
            NEW_PASSWORD,
            role="system_user",
            current_hash=current_hash,
        )

    assert hashed
    assert hashed != current_hash
