"""Unit tests for security/password_policy.py — password strength, common passwords, lockout, history."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from security.password_policy import (
    is_common_password,
    validate_password_strength,
    PasswordStrengthResult,
    MIN_PASSWORD_LENGTH,
    MAX_FAILED_ATTEMPTS,
    LOCKOUT_DURATION_SECONDS,
    PASSWORD_HISTORY_COUNT,
    _has_sequential_chars,
    _has_repeated_chars,
)


# ---------------------------------------------------------------------------
# Common password checking
# ---------------------------------------------------------------------------


class TestCommonPasswords:
    def test_common_password_detected(self):
        assert is_common_password("password") is True
        assert is_common_password("123456") is True
        assert is_common_password("qwerty") is True
        assert is_common_password("letmein") is True
        assert is_common_password("admin") is True

    def test_common_password_case_insensitive(self):
        assert is_common_password("PASSWORD") is True
        assert is_common_password("Password") is True
        assert is_common_password("QWERTY") is True

    def test_uncommon_password_passes(self):
        assert is_common_password("xK9$mPq2!vLn") is False
        assert is_common_password("MyUnique#Pass99") is False


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------


class TestPasswordStrength:
    def test_strong_password_passes(self):
        result = validate_password_strength("Str0ng!Pass#99")
        assert result.is_valid is True
        assert result.errors == []
        assert result.score >= 3

    def test_too_short(self):
        result = validate_password_strength("Ab1!")
        assert result.is_valid is False
        assert any("at least" in e for e in result.errors)

    def test_too_long(self):
        result = validate_password_strength("A" * 129 + "a1!")
        assert result.is_valid is False
        assert any("exceed" in e for e in result.errors)

    def test_missing_uppercase(self):
        result = validate_password_strength("lowercase1!")
        assert result.is_valid is False
        assert any("uppercase" in e for e in result.errors)

    def test_missing_lowercase(self):
        result = validate_password_strength("UPPERCASE1!")
        assert result.is_valid is False
        assert any("lowercase" in e for e in result.errors)

    def test_missing_digit(self):
        result = validate_password_strength("NoDigits!Here")
        assert result.is_valid is False
        assert any("digit" in e for e in result.errors)

    def test_missing_special_char(self):
        result = validate_password_strength("NoSpecial1Here")
        assert result.is_valid is False
        assert any("special" in e for e in result.errors)

    def test_common_password_blocked(self):
        result = validate_password_strength("password")
        assert result.is_valid is False
        assert any("common" in e or "breaches" in e for e in result.errors)

    def test_sequential_chars_blocked(self):
        result = validate_password_strength("Abcd1234!@#$")
        assert result.is_valid is False
        assert any("sequential" in e for e in result.errors)

    def test_repeated_chars_blocked(self):
        result = validate_password_strength("Aaaa1234!@#$")
        assert result.is_valid is False
        assert any("repeated" in e for e in result.errors)

    def test_result_is_pydantic_model(self):
        result = validate_password_strength("Test1ng!Strong")
        assert isinstance(result, PasswordStrengthResult)
        assert isinstance(result.score, int)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_sequential_ascending(self):
        assert _has_sequential_chars("abcd", 4) is True
        assert _has_sequential_chars("1234", 4) is True

    def test_sequential_descending(self):
        assert _has_sequential_chars("dcba", 4) is True
        assert _has_sequential_chars("4321", 4) is True

    def test_no_sequential(self):
        assert _has_sequential_chars("aXbY", 4) is False

    def test_repeated_chars(self):
        assert _has_repeated_chars("aaaa", 4) is True
        assert _has_repeated_chars("1111", 4) is True

    def test_no_repeated_chars(self):
        assert _has_repeated_chars("abca", 4) is False


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------


class _FakeCollection:
    """Fake MongoDB collection for testing."""

    def __init__(self, find_one_val=None, find_and_update_val=None):
        self._find_one_val = find_one_val
        self._find_and_update_val = find_and_update_val

    async def find_one(self, *a, **kw):
        return self._find_one_val

    async def find_one_and_update(self, *a, **kw):
        return self._find_and_update_val

    async def update_one(self, *a, **kw):
        pass

    async def delete_one(self, *a, **kw):
        pass


@pytest.mark.asyncio
async def test_check_login_lockout_not_locked():
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=_FakeCollection(find_one_val=None))

    with patch("security.password_policy.db", fake_db):
        # Re-import to get patched version
        from security.password_policy import check_login_lockout
        result = await check_login_lockout("test@example.com")
        assert result is None


@pytest.mark.asyncio
async def test_check_login_lockout_locked():
    import time
    locked_record = {
        "identifier": "test@example.com",
        "failed_count": 5,
        "locked_until": int(time.time()) + 600,
    }
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=_FakeCollection(find_one_val=locked_record))

    with patch("security.password_policy.db", fake_db):
        from security.password_policy import check_login_lockout
        result = await check_login_lockout("test@example.com")
        assert result is not None
        assert result["locked"] is True
        assert result["remaining_seconds"] > 0


@pytest.mark.asyncio
async def test_record_failed_login_increments():
    fake_result = {"identifier": "test@example.com", "failed_count": 2}
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(
        return_value=_FakeCollection(find_and_update_val=fake_result)
    )

    with patch("security.password_policy.db", fake_db):
        from security.password_policy import record_failed_login
        result = await record_failed_login("test@example.com")
        assert result["locked"] is False
        assert result["failed_count"] == 2


@pytest.mark.asyncio
async def test_record_failed_login_triggers_lockout():
    fake_result = {"identifier": "test@example.com", "failed_count": MAX_FAILED_ATTEMPTS}
    fake_col = _FakeCollection(find_and_update_val=fake_result)
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=fake_col)

    with patch("security.password_policy.db", fake_db):
        from security.password_policy import record_failed_login
        result = await record_failed_login("test@example.com")
        assert result["locked"] is True


# ---------------------------------------------------------------------------
# Password history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_password_history_no_reuse():
    """Password not in history should return True (safe to use)."""
    class FakeCursor:
        def sort(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def to_list(self, length): return []

    fake_col = MagicMock()
    fake_col.find = MagicMock(return_value=FakeCursor())
    fake_db = MagicMock()
    fake_db.__getitem__ = MagicMock(return_value=fake_col)

    with patch("security.password_policy.db", fake_db):
        from security.password_policy import check_password_history
        result = await check_password_history("user1", "NewStrongP@ss1", role="admin")
        assert result is True


# ---------------------------------------------------------------------------
# Schema integration — AdminCreate now validates password
# ---------------------------------------------------------------------------


class TestSchemaPasswordValidation:
    def test_admin_create_weak_password_fails(self):
        from schemas.admin_schema import AdminCreate
        with pytest.raises(ValueError, match="at least"):
            AdminCreate(
                full_name="Test",
                email="test@example.com",
                password="weak",
                invited_by="admin1",
            )

    def test_admin_create_common_password_fails(self):
        from schemas.admin_schema import AdminCreate
        with pytest.raises(ValueError, match="common"):
            AdminCreate(
                full_name="Test",
                email="test@example.com",
                password="password",
                invited_by="admin1",
            )

    def test_admin_create_strong_password_succeeds(self):
        from schemas.admin_schema import AdminCreate
        admin = AdminCreate(
            full_name="Test Admin",
            email="test@example.com",
            password="MyStr0ng!Pass#2024",
            invited_by="admin1",
        )
        # Password should be hashed (bytes)
        assert isinstance(admin.password, bytes)

    def test_system_user_create_weak_password_fails(self):
        from schemas.system_user_schema import SystemUserCreate
        with pytest.raises(ValueError):
            SystemUserCreate(
                tenant_id="t1",
                full_name="Test User",
                email="user@example.com",
                role="receptionist",
                password_hash="weak",
            )

    def test_system_user_create_strong_password_succeeds(self):
        from schemas.system_user_schema import SystemUserCreate
        user = SystemUserCreate(
            tenant_id="t1",
            full_name="Test User",
            email="user@example.com",
            role="receptionist",
            password_hash="MyStr0ng!Pass#2024",
        )
        assert isinstance(user.password_hash, bytes)
