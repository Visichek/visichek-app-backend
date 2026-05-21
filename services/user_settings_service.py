from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from repositories.user_settings_repo import (
    create_user_settings,
    get_user_settings,
    update_user_settings,
    get_user_preferences,
    set_user_preference,
    count_user_preference_keys,
)
from schemas.imports import UserType
from schemas.user_settings_schema import (
    UserSettingsCreate,
    UserSettingsUpdate,
    UserSettingsOut,
)

MAX_PREFERENCE_KEYS = 50


async def retrieve_or_create_settings(
    user_id: str, user_type: UserType
) -> UserSettingsOut:
    """Get user settings, auto-creating defaults on first access (upsert pattern)."""
    existing = await get_user_settings({"user_id": user_id, "user_type": user_type})
    if existing:
        return existing

    # First access — create default settings
    defaults = UserSettingsCreate(user_id=user_id, user_type=user_type)
    return await create_user_settings(defaults)


async def update_settings(
    user_id: str,
    user_type: UserType,
    data: UserSettingsUpdate,
) -> UserSettingsOut:
    """Update user settings, creating defaults first if needed."""
    # Ensure settings record exists
    await retrieve_or_create_settings(user_id, user_type)

    result = await update_user_settings(
        {"user_id": user_id, "user_type": user_type},
        data,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to update settings")
    return result


async def retrieve_preferences(user_id: str, user_type: UserType) -> dict[str, Any]:
    """Get all preferences for a user."""
    return await get_user_preferences(user_id, user_type)


async def save_preference(
    user_id: str,
    user_type: UserType,
    key: str,
    value: Any,
) -> dict[str, Any]:
    """Save a single preference key-value pair with limits enforced."""
    # Check key count limit
    current_count = await count_user_preference_keys(user_id, user_type)
    existing_prefs = await get_user_preferences(user_id, user_type)

    if key not in existing_prefs and current_count >= MAX_PREFERENCE_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum of {MAX_PREFERENCE_KEYS} preference keys allowed",
        )

    return await set_user_preference(user_id, user_type, key, value)
