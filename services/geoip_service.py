"""Best-effort IP → location resolution for the sessions table.

The sessions UI has a "Location / IP" column that only ever showed the
raw IP because nothing populated ``sessions.location``. This service
resolves a public IP to a human-readable "City, Region, Country" string
via the free ipwho.is HTTPS API, cached in Redis so each distinct IP is
looked up at most once per week.

Design constraints:

* **Never blocks auth.** Callers sit on the fire-and-forget session
  recording path; every failure mode (private IP, network error,
  provider outage, Redis down) returns ``None`` and the session simply
  keeps showing the IP.
* **Private/reserved IPs are skipped** — office LANs and localhost have
  no meaningful geo answer, and leaking RFC1918 addresses to an external
  API would be wrong anyway.
* **Failures are negative-cached** for a few hours so a provider outage
  doesn't turn every login into a 3-second timeout.
* Disabled entirely when ``ENV=testing`` (unit tests must not hit the
  network) or ``GEOIP_ENABLED=false``.
"""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "geoip:"
_CACHE_TTL_HIT_SECONDS = 7 * 24 * 3600
_CACHE_TTL_MISS_SECONDS = 6 * 3600
_LOOKUP_TIMEOUT_SECONDS = 3.0
# Sentinel cached for known-miss IPs so we don't re-query the provider.
_MISS_SENTINEL = "__none__"


def _is_lookupable_ip(ip: str | None) -> bool:
    """Only public, well-formed unicast addresses are worth a lookup."""
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def _format_location(data: dict) -> str | None:
    """Build 'City, Region, Country' skipping blanks and adjacent dupes
    (city-states like Singapore return the same value for all three)."""
    parts = [str(data.get(key) or "").strip() for key in ("city", "region", "country")]
    deduped: list[str] = []
    for part in parts:
        if part and (not deduped or deduped[-1] != part):
            deduped.append(part)
    return ", ".join(deduped) or None


async def _fetch_location(ip: str) -> str | None:
    """Query ipwho.is. Returns None on any failure — never raises."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_SECONDS) as client:
            response = await client.get(f"https://ipwho.is/{ip}")
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or not data.get("success", False):
            return None
        return _format_location(data)
    except Exception:
        logger.debug("geoip: lookup failed for %s", ip, exc_info=True)
        return None


async def lookup_ip_location(ip: str | None) -> str | None:
    """Resolve a public IP to 'City, Region, Country' — best-effort.

    Redis-cached per IP (hits for a week, misses for a few hours).
    Returns ``None`` for private/malformed IPs, in the testing env, when
    ``GEOIP_ENABLED=false``, or on any provider/cache failure.
    """
    from core.settings import get_settings

    if not _is_lookupable_ip(ip):
        return None

    settings = get_settings()
    if settings.env == "testing" or not getattr(settings, "geoip_enabled", True):
        return None

    assert ip is not None
    ip = ip.strip()
    cache_key = f"{_CACHE_PREFIX}{ip}"

    try:
        from core.redis_cache import cache_db

        cached = cache_db.get(cache_key)
        if cached is not None:
            value = cached.decode() if isinstance(cached, bytes) else str(cached)
            return None if value in ("", _MISS_SENTINEL) else value
    except Exception:
        # Redis down → fall through to a live lookup; still best-effort.
        pass

    location = await _fetch_location(ip)

    try:
        from core.redis_cache import cache_db

        cache_db.set(
            cache_key,
            location or _MISS_SENTINEL,
            ex=_CACHE_TTL_HIT_SECONDS if location else _CACHE_TTL_MISS_SECONDS,
        )
    except Exception:
        pass

    return location
