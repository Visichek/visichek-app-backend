"""Queued write handler for personal user settings.

User settings are upsert-style (one record per (user_id, user_type))
so the ``resource_id`` passed to the writer is the user_id. Per-user
precompute is not registered — the record is small and fetching it
directly is cheap — but writes still go through the queue for
consistency with the rest of the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from core.queue.write_pipeline import write_handler
from schemas.imports import UserType
from schemas.user_settings_schema import UserSettingsUpdate
from services.user_settings_service import update_settings

logger = logging.getLogger(__name__)


@write_handler("user_settings.update")
async def _user_settings_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    user_id = data.pop("user_id", resource_id) or resource_id
    user_type = UserType(data.pop("user_type", "") or "system_user")
    upd = UserSettingsUpdate(**data)
    result = await update_settings(user_id=user_id, user_type=user_type, data=upd)
    return {"id": result.id, "user_id": user_id}
