"""Queued write handler for ephemeral user location updates.

The gate checks fire-and-forget ``user_location.update`` on every
authenticated request that carries an ``X-User-Location`` header. The
payload lands in Redis (see :mod:`core.geofencing`) with a 10-minute
TTL — there is no MongoDB collection and no history, because this is
ephemeral presence data used only for visitor geofence enforcement.

The writer returns a small result for the queue job log but the real
work is already done by :func:`core.geofencing.store_user_location`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.geofencing import store_user_location
from core.queue.write_pipeline import write_handler

logger = logging.getLogger(__name__)


@write_handler("user_location.update")
async def _user_location_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = str(data.get("user_id") or resource_id)
    tenant_id = data.get("tenant_id")
    role = str(data.get("role") or "")
    lat = data.get("lat")
    lng = data.get("lng")

    if lat is None or lng is None or not user_id:
        return {"stored": False, "reason": "missing_fields"}

    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return {"stored": False, "reason": "invalid_coords"}

    accuracy = data.get("accuracy_m")
    try:
        accuracy_f = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy_f = None

    ts = data.get("ts")
    try:
        ts_i = int(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_i = None

    store_user_location(
        user_id=user_id,
        tenant_id=str(tenant_id) if tenant_id else None,
        role=role,
        lat=lat_f,
        lng=lng_f,
        accuracy_m=accuracy_f,
        ts=ts_i,
    )
    return {"stored": True, "user_id": user_id}
