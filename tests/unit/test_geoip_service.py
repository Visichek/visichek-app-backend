"""Geo-IP lookup for the sessions "Location" column.

The lookup must be strictly best-effort: private/malformed IPs are never
sent to the provider, the testing env never touches the network, results
are Redis-cached, and every failure mode degrades to ``None`` (the UI
then falls back to showing the raw IP).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.geoip_service import (
    _MISS_SENTINEL,
    _format_location,
    _is_lookupable_ip,
    lookup_ip_location,
)

pytestmark = pytest.mark.unit


# ── IP eligibility ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        None,
        "",
        "not-an-ip",
        "192.168.1.10",
        "10.0.0.5",
        "172.16.3.1",
        "127.0.0.1",
        "169.254.1.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
    ],
)
def test_non_lookupable_ips(ip) -> None:
    assert _is_lookupable_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "102.89.33.4", "2606:4700::1111"])
def test_public_ips_are_lookupable(ip) -> None:
    assert _is_lookupable_ip(ip) is True


# ── Formatting ───────────────────────────────────────────────────────


def test_format_location_full() -> None:
    data = {"city": "Lagos", "region": "Lagos", "country": "Nigeria"}
    # Adjacent duplicates (city == region) collapse.
    assert _format_location(data) == "Lagos, Nigeria"


def test_format_location_missing_city() -> None:
    data = {"city": "", "region": "Kaduna", "country": "Nigeria"}
    assert _format_location(data) == "Kaduna, Nigeria"


def test_format_location_all_blank() -> None:
    assert _format_location({}) is None


# ── lookup_ip_location gating ────────────────────────────────────────


async def test_lookup_returns_none_in_testing_env() -> None:
    """The test suite runs with ENV=testing — no mocking needed, and this
    doubles as proof the suite can never hit the network."""
    assert await lookup_ip_location("8.8.8.8") is None


async def test_lookup_returns_none_for_private_ip() -> None:
    assert await lookup_ip_location("192.168.0.1") is None


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_disabled_by_flag(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=False)
    assert await lookup_ip_location("8.8.8.8") is None
    mock_fetch.assert_not_awaited()


# ── lookup_ip_location behavior (production env, mocked provider) ────


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_fetches_and_caches_hit(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=True)
    mock_cache.get.return_value = None
    mock_fetch.return_value = "Lagos, Nigeria"

    assert await lookup_ip_location("102.89.33.4") == "Lagos, Nigeria"

    mock_fetch.assert_awaited_once_with("102.89.33.4")
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert args == ("geoip:102.89.33.4", "Lagos, Nigeria")
    assert kwargs["ex"] == 7 * 24 * 3600


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_serves_cached_value_without_fetch(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=True)
    mock_cache.get.return_value = b"Abuja, Nigeria"

    assert await lookup_ip_location("102.89.33.4") == "Abuja, Nigeria"
    mock_fetch.assert_not_awaited()


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_negative_cache_short_ttl(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=True)
    mock_cache.get.return_value = None
    mock_fetch.return_value = None

    assert await lookup_ip_location("102.89.33.4") is None

    args, kwargs = mock_cache.set.call_args
    assert args == ("geoip:102.89.33.4", _MISS_SENTINEL)
    assert kwargs["ex"] == 6 * 3600


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_cached_miss_sentinel_returns_none(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=True)
    mock_cache.get.return_value = _MISS_SENTINEL.encode()

    assert await lookup_ip_location("102.89.33.4") is None
    mock_fetch.assert_not_awaited()


@patch("services.geoip_service._fetch_location", new_callable=AsyncMock)
@patch("core.redis_cache.cache_db")
@patch("core.settings.get_settings")
async def test_lookup_survives_redis_failure(
    mock_settings: MagicMock, mock_cache: MagicMock, mock_fetch: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(env="production", geoip_enabled=True)
    mock_cache.get.side_effect = RuntimeError("redis down")
    mock_cache.set.side_effect = RuntimeError("redis down")
    mock_fetch.return_value = "Lagos, Nigeria"

    assert await lookup_ip_location("102.89.33.4") == "Lagos, Nigeria"


# ── record_session wiring ────────────────────────────────────────────


@patch("services.session_service.create_session", new_callable=AsyncMock)
@patch("services.geoip_service.lookup_ip_location", new_callable=AsyncMock)
async def test_record_session_stores_resolved_location(
    mock_lookup: AsyncMock, mock_create: AsyncMock
) -> None:
    from schemas.imports import UserType
    from services.session_service import record_session

    mock_lookup.return_value = "Lagos, Nigeria"
    mock_create.return_value = MagicMock()

    await record_session(
        user_id="u1",
        user_type=UserType.SYSTEM_USER,
        access_token_id="tok1",
        ip_address="102.89.33.4",
        user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126 Safari/537.36",
    )

    mock_lookup.assert_awaited_once_with("102.89.33.4")
    create_call = mock_create.await_args
    assert create_call is not None
    assert create_call.args[0].location == "Lagos, Nigeria"


@patch("services.session_service.rotate_session_access_token", new_callable=AsyncMock)
@patch("services.geoip_service.lookup_ip_location", new_callable=AsyncMock)
async def test_record_session_rotation_carries_location(
    mock_lookup: AsyncMock, mock_rotate: AsyncMock
) -> None:
    from schemas.imports import UserType
    from services.session_service import record_session

    mock_lookup.return_value = "Abuja, Nigeria"
    mock_rotate.return_value = MagicMock()

    await record_session(
        user_id="u1",
        user_type=UserType.SYSTEM_USER,
        access_token_id="tok2",
        ip_address="102.89.33.4",
        user_agent="ua",
        previous_access_token_id="tok1",
    )

    rotate_call = mock_rotate.await_args
    assert rotate_call is not None
    assert rotate_call.kwargs["location"] == "Abuja, Nigeria"
