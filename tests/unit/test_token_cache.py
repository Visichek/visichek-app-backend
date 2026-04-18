"""Unit tests for the in-process access-token cache."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core import token_cache


def _fake_token(token_id: str = "t_1") -> MagicMock:
    m = MagicMock()
    m.id = token_id
    return m


@pytest.fixture(autouse=True)
def _reset_cache():
    token_cache.clear()
    yield
    token_cache.clear()


def test_put_then_get_returns_same_instance() -> None:
    tok = _fake_token()
    token_cache.put("jwt_abc", tok, ttl=30)
    assert token_cache.get("jwt_abc") is tok


def test_get_returns_none_on_miss() -> None:
    assert token_cache.get("never_stored") is None


def test_expired_entry_is_evicted_on_get() -> None:
    tok = _fake_token()
    token_cache.put("jwt_abc", tok, ttl=0)
    time.sleep(0.01)
    assert token_cache.get("jwt_abc") is None
    # Stats should reflect the drop.
    assert token_cache.stats()["entries"] == 0


def test_invalidate_removes_single_entry() -> None:
    token_cache.put("jwt_abc", _fake_token("t_1"))
    token_cache.put("jwt_def", _fake_token("t_2"))
    token_cache.invalidate("jwt_abc")
    assert token_cache.get("jwt_abc") is None
    assert token_cache.get("jwt_def") is not None


def test_invalidate_by_token_id_drops_every_form() -> None:
    # Same backing token can be keyed by multiple JWT strings over its life
    # (e.g. one request used the raw ObjectId, another used the JWT wrapping
    # the same id). Logout needs to drop all of them.
    tok = _fake_token("shared")
    token_cache.put("jwt_form_1", tok)
    token_cache.put("jwt_form_2", tok)
    token_cache.put("unrelated", _fake_token("other"))

    token_cache.invalidate_by_token_id("shared")

    assert token_cache.get("jwt_form_1") is None
    assert token_cache.get("jwt_form_2") is None
    assert token_cache.get("unrelated") is not None


def test_clear_empties_cache() -> None:
    token_cache.put("a", _fake_token("t1"))
    token_cache.put("b", _fake_token("t2"))
    token_cache.clear()
    assert token_cache.stats()["entries"] == 0
    assert token_cache.stats()["token_ids_tracked"] == 0


def test_lru_eviction_keeps_max_size() -> None:
    # Temporarily shrink the limit so the test doesn't need 2001 inserts.
    original = token_cache._MAX_ENTRIES
    token_cache._MAX_ENTRIES = 3  # type: ignore[attr-defined]
    try:
        token_cache.put("k1", _fake_token("t1"))
        token_cache.put("k2", _fake_token("t2"))
        token_cache.put("k3", _fake_token("t3"))
        # Touch k1 so it becomes most-recently-used.
        _ = token_cache.get("k1")
        # Inserting k4 should evict the LRU (k2), not k1.
        token_cache.put("k4", _fake_token("t4"))
        assert token_cache.get("k1") is not None
        assert token_cache.get("k2") is None
        assert token_cache.get("k3") is not None
        assert token_cache.get("k4") is not None
    finally:
        token_cache._MAX_ENTRIES = original  # type: ignore[attr-defined]
