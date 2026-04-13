from __future__ import annotations

from typing import List

from bson import ObjectId
from fastapi import HTTPException

from repositories.session_repo import (
    create_session,
    get_sessions,
    delete_session,
    delete_sessions,
)
from schemas.session_schema import DeviceType, SessionCreate, SessionOut


def _detect_device_type(user_agent: str | None) -> DeviceType:
    """Simple device detection from user-agent string."""
    if not user_agent:
        return DeviceType.UNKNOWN
    ua = user_agent.lower()
    if any(kw in ua for kw in ("iphone", "android", "mobile")):
        return DeviceType.MOBILE
    if any(kw in ua for kw in ("ipad", "tablet")):
        return DeviceType.TABLET
    if any(kw in ua for kw in ("windows", "macintosh", "linux", "x11")):
        return DeviceType.DESKTOP
    return DeviceType.UNKNOWN


def parse_device_label(user_agent: str | None) -> str:
    """Parse user-agent into a human-readable label like 'Chrome (Windows)'."""
    if not user_agent:
        return "Unknown device"

    ua = user_agent

    # Detect browser
    browser = "Unknown browser"
    if "Edg/" in ua or "Edge/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Chrome/" in ua and "Safari/" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"

    # Detect OS
    os_name = "Unknown"
    if "Windows" in ua:
        os_name = "Windows"
    elif "Macintosh" in ua or "Mac OS" in ua:
        os_name = "macOS"
    elif "iPhone" in ua:
        os_name = "iOS"
    elif "iPad" in ua:
        os_name = "iPadOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "Linux" in ua:
        os_name = "Linux"

    return f"{browser} ({os_name})"


async def record_session(
    user_id: str,
    user_type: str,
    access_token_id: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SessionOut:
    """Record a new session after successful login."""
    device_type = _detect_device_type(user_agent)
    device_label = parse_device_label(user_agent)
    data = SessionCreate(
        user_id=user_id,
        user_type=user_type,
        access_token_id=access_token_id,
        ip_address=ip_address,
        user_agent=user_agent,
        device_type=device_type,
        device=device_label,
    )
    return await create_session(data)


async def retrieve_sessions(
    user_id: str,
    user_type: str,
    current_token_id: str | None = None,
) -> List[SessionOut]:
    """List active sessions for a user, marking the current one."""
    sessions = await get_sessions({"user_id": user_id, "user_type": user_type})

    if current_token_id:
        for session in sessions:
            if session.access_token_id == current_token_id:
                session.is_current = True

    return sessions


async def revoke_session(
    session_id: str,
    user_id: str,
    user_type: str,
) -> None:
    """Revoke a specific session."""
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    result = await delete_session(
        {"_id": ObjectId(session_id), "user_id": user_id, "user_type": user_type}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")


async def revoke_all_sessions_except_current(
    user_id: str,
    user_type: str,
    current_token_id: str,
) -> int:
    """Revoke all sessions except the current one. Returns count revoked."""
    return await delete_sessions(
        {
            "user_id": user_id,
            "user_type": user_type,
            "access_token_id": {"$ne": current_token_id},
        }
    )
