from __future__ import annotations

from typing import List

from bson import ObjectId
from fastapi import HTTPException

from repositories.session_repo import (
    create_session,
    get_session,
    get_sessions,
    delete_session,
    delete_sessions,
    rotate_session_access_token,
)
from repositories.tokens_repo import (
    delete_access_token,
    delete_access_tokens_by_ids,
    delete_refresh_tokens_by_previous_access_token,
    filter_existing_access_token_ids,
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
    previous_access_token_id: str | None = None,
) -> SessionOut:
    """Record a session after a successful login or token refresh.

    On **login** (``previous_access_token_id`` is ``None``) a fresh row is
    inserted. On **refresh** the device already has a session row keyed by
    the now-expired access token; we rotate that row's ``access_token_id``
    to the new value instead of inserting a duplicate. If no row matches
    the expired id (e.g. the session predates rotation, or was revoked) we
    fall back to inserting a new row so the device still shows up.
    """
    if previous_access_token_id:
        rotated = await rotate_session_access_token(
            old_access_token_id=previous_access_token_id,
            new_access_token_id=access_token_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if rotated is not None:
            return rotated

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
    """List active sessions for a user, marking the current one.

    Only sessions whose backing access token still exists are returned. A
    token refresh deletes the superseded access token but leaves its session
    row behind (rotation re-points the row at the new token, but any row a
    rotation missed — plus rows from logins on networks the user has since
    left — keep pointing at deleted tokens). Those rows are dead: they can
    never authenticate a request again, so we filter them out of the list
    and best-effort prune them. The current session is always retained — we
    just authenticated with its token.
    """
    sessions = await get_sessions({"user_id": user_id, "user_type": user_type})

    token_ids = [s.access_token_id for s in sessions if s.access_token_id]
    live_token_ids = await filter_existing_access_token_ids(token_ids)

    live_sessions: List[SessionOut] = []
    dead_session_ids: List[str] = []
    for session in sessions:
        is_current = (
            current_token_id is not None and session.access_token_id == current_token_id
        )
        if session.access_token_id in live_token_ids or is_current:
            if is_current:
                session.is_current = True
            live_sessions.append(session)
        elif session.id:
            dead_session_ids.append(session.id)

    if dead_session_ids:
        try:
            await delete_sessions(
                {"_id": {"$in": [ObjectId(sid) for sid in dead_session_ids]}}
            )
        except Exception:
            # Pruning is housekeeping — never let it break the read path.
            pass

    return live_sessions


async def revoke_session(
    session_id: str,
    user_id: str,
    user_type: str,
) -> None:
    """Revoke a specific session — and the auth tokens that back it.

    Deleting only the ``sessions`` row used to leave the device's access
    + refresh tokens valid until natural expiry, so "revoke" lied to the
    user. We now also drop the ``accessToken`` row and any refresh tokens
    chained off it, then evict the in-process token cache so the next
    request from that device is rejected within the cache TTL window.
    """
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session = await get_session(
        {"_id": ObjectId(session_id), "user_id": user_id, "user_type": user_type}
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    access_token_id = session.access_token_id
    if access_token_id:
        # Refresh tokens first — if we drop the access row but leave the
        # refresh row in place, the device's next /refresh call mints a
        # brand-new access row and the revoke effectively did nothing.
        await delete_refresh_tokens_by_previous_access_token(access_token_id)
        try:
            await delete_access_token(access_token_id)
        except Exception:
            # delete_access_token raises if the id is malformed. The
            # session row is still removed below — auth failure on
            # subsequent requests will surface the issue.
            pass

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
    """Revoke every session for ``user_id`` except the one calling us.

    Same fix as :func:`revoke_session` applied in bulk: collect every
    revoked session's ``access_token_id``, drop the matching access
    rows + their refresh chains, then delete the session rows. Returns
    the number of sessions revoked.
    """
    targets = await get_sessions(
        {
            "user_id": user_id,
            "user_type": user_type,
            "access_token_id": {"$ne": current_token_id},
        }
    )
    access_token_ids = [s.access_token_id for s in targets if s.access_token_id]

    for atid in access_token_ids:
        await delete_refresh_tokens_by_previous_access_token(atid)
    if access_token_ids:
        await delete_access_tokens_by_ids(access_token_ids)

    return await delete_sessions(
        {
            "user_id": user_id,
            "user_type": user_type,
            "access_token_id": {"$ne": current_token_id},
        }
    )
