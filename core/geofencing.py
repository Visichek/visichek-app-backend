"""Geofencing helpers.

Two concerns live here:

1. **Ingestion** — parse ``X-User-Location`` headers off authenticated
   requests (``lat,lng[,accuracy_m]``) so gate checks can fire-and-forget
   a location update without dragging raw request parsing into the auth
   dependencies themselves.
2. **Enforcement** — haversine distance, a Redis scan of active approvers
   for a given tenant, and the single ``enforce_visitor_geofence`` entry
   point that the visitor check-in service calls before creating a
   check-in.

Ephemeral presence data lives in Redis only. There is no Mongo
collection and no history — if a staff member stops hitting the API,
their location disappears at TTL (10 min). See
``backend-docs/geofencing.md`` for the wider design.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Iterable, Optional, cast

from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

LOCATION_HEADER = "X-User-Location"
LOCATION_TTL_SECONDS = 600  # 10 minutes — matches "active for 10 min"
_LOCATION_PREFIX = "user_location"
_EARTH_RADIUS_METERS = 6_371_000.0


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def parse_location_header(raw: Optional[str]) -> Optional[dict[str, float]]:
    """Parse an ``X-User-Location`` value into ``{lat, lng, accuracy_m?}``.

    Accepted formats (all comma-separated, whitespace tolerated):

    * ``"<lat>,<lng>"``
    * ``"<lat>,<lng>,<accuracy_m>"``

    Returns ``None`` on any parse failure so callers can treat "missing"
    and "malformed" identically — the write simply isn't enqueued.
    """
    if not raw:
        return None
    try:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) < 2:
            return None
        lat = float(parts[0])
        lng = float(parts[1])
    except (TypeError, ValueError):
        return None

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None

    result: dict[str, float] = {"lat": lat, "lng": lng}
    if len(parts) >= 3:
        try:
            accuracy = float(parts[2])
            if accuracy >= 0:
                result["accuracy_m"] = accuracy
        except (TypeError, ValueError):
            pass
    return result


# ---------------------------------------------------------------------------
# Redis storage
# ---------------------------------------------------------------------------


def _location_key(user_id: str) -> str:
    return f"{_LOCATION_PREFIX}:{user_id}"


def _tenant_index_key(tenant_id: str) -> str:
    return f"{_LOCATION_PREFIX}:tenant:{tenant_id}"


def store_user_location(
    *,
    user_id: str,
    tenant_id: Optional[str],
    role: str,
    lat: float,
    lng: float,
    accuracy_m: Optional[float] = None,
    ts: Optional[int] = None,
) -> None:
    """Persist a user's latest location in Redis with a 10-minute TTL.

    Call from :mod:`services.user_location_writer` — never from a
    request-path. Failures are swallowed so a bad Redis never blocks a
    background task.
    """
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "lat": lat,
        "lng": lng,
        "accuracy_m": accuracy_m,
        "ts": int(ts if ts is not None else time.time()),
    }
    try:
        cache_db.setex(
            _location_key(user_id), LOCATION_TTL_SECONDS, json.dumps(payload)
        )
        # Secondary index so the enforcement path can find tenant approvers
        # in O(1) without scanning the whole keyspace. Each member expires
        # from the set when its primary key is evicted — we re-add every
        # write so it stays warm as long as the user is active.
        if tenant_id:
            cache_db.sadd(_tenant_index_key(tenant_id), user_id)
            cache_db.expire(_tenant_index_key(tenant_id), LOCATION_TTL_SECONDS * 2)
    except Exception:
        logger.warning(
            "store_user_location failed for user_id=%s tenant_id=%s",
            user_id,
            tenant_id,
            exc_info=True,
        )


def _iter_tenant_locations(tenant_id: str) -> Iterable[dict[str, Any]]:
    """Yield every live location for the tenant, pruning dead index entries."""
    try:
        raw_members = (
            cast(set[str], cache_db.smembers(_tenant_index_key(tenant_id))) or set()
        )
    except Exception:
        logger.warning(
            "geofencing: smembers failed tenant_id=%s", tenant_id, exc_info=True
        )
        return

    stale: list[str] = []
    for user_id in raw_members:
        try:
            raw = cast(Optional[str], cache_db.get(_location_key(user_id)))
        except Exception:
            raw = None
        if not raw:
            stale.append(user_id)
            continue
        try:
            data = json.loads(raw)
        except Exception:
            stale.append(user_id)
            continue
        yield data

    if stale:
        try:
            cache_db.srem(_tenant_index_key(tenant_id), *stale)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points in metres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_METERS * c


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


APPROVER_ROLES: frozenset[str] = frozenset(
    {"receptionist", "super_admin", "dept_admin"}
)


class GeofenceCheckResult:
    """Outcome of a geofence enforcement decision.

    ``allowed=True`` means either geofencing is disabled for the tenant
    or the visitor is inside the configured radius. ``allowed=False``
    means the submit should be rejected.
    """

    __slots__ = ("allowed", "reason", "details")

    def __init__(
        self,
        *,
        allowed: bool,
        reason: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.details = details or {}


def check_visitor_within_geofence(
    *,
    tenant_settings: Any,
    visitor_lat: Optional[float],
    visitor_lng: Optional[float],
) -> GeofenceCheckResult:
    """Decide whether a visitor check-in is allowed under the tenant's geofence.

    Resolution order:

    1. If ``geofencing_enabled`` is false, allow.
    2. Require visitor lat/lng — otherwise reject with a missing-coords
       reason so the kiosk can prompt for location access.
    3. If the tenant has a fixed reference point configured, measure
       against it (deterministic, preferred).
    4. Otherwise, scan active approvers and allow if any one of them is
       within the radius.
    5. If no approver is live, reject — the tenant is effectively closed.
    """
    if not getattr(tenant_settings, "geofencing_enabled", False):
        return GeofenceCheckResult(allowed=True, reason="disabled")

    radius = int(getattr(tenant_settings, "geofencing_radius_meters", 50) or 50)

    if visitor_lat is None or visitor_lng is None:
        return GeofenceCheckResult(
            allowed=False,
            reason="missing_visitor_location",
            details={"radius_m": radius},
        )

    ref_lat = getattr(tenant_settings, "geofencing_reference_lat", None)
    ref_lng = getattr(tenant_settings, "geofencing_reference_lng", None)
    if ref_lat is not None and ref_lng is not None:
        distance = haversine_meters(visitor_lat, visitor_lng, ref_lat, ref_lng)
        if distance <= radius:
            return GeofenceCheckResult(
                allowed=True,
                reason="within_reference_point",
                details={"distance_m": round(distance, 1), "radius_m": radius},
            )
        return GeofenceCheckResult(
            allowed=False,
            reason="outside_reference_point",
            details={"distance_m": round(distance, 1), "radius_m": radius},
        )

    tenant_id = getattr(tenant_settings, "tenant_id", None)
    if not tenant_id:
        # Can't resolve approvers without a tenant id — treat as misconfig.
        return GeofenceCheckResult(
            allowed=False,
            reason="tenant_misconfigured",
            details={"radius_m": radius},
        )

    nearest: Optional[tuple[float, str]] = None
    for entry in _iter_tenant_locations(str(tenant_id)):
        if entry.get("role") not in APPROVER_ROLES:
            continue
        try:
            lat = float(entry["lat"])
            lng = float(entry["lng"])
        except (TypeError, ValueError, KeyError):
            continue
        distance = haversine_meters(visitor_lat, visitor_lng, lat, lng)
        if distance <= radius:
            return GeofenceCheckResult(
                allowed=True,
                reason="within_approver_radius",
                details={
                    "approver_user_id": entry.get("user_id"),
                    "distance_m": round(distance, 1),
                    "radius_m": radius,
                },
            )
        if nearest is None or distance < nearest[0]:
            nearest = (distance, str(entry.get("user_id") or ""))

    if nearest is None:
        return GeofenceCheckResult(
            allowed=False,
            reason="no_active_approvers",
            details={"radius_m": radius},
        )
    return GeofenceCheckResult(
        allowed=False,
        reason="outside_approver_radius",
        details={
            "nearest_distance_m": round(nearest[0], 1),
            "radius_m": radius,
        },
    )
