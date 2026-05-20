from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from core.response_envelope import success_payload
from core.settings import get_settings

logger = logging.getLogger(__name__)

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"

ACCESS_TOKEN_MAX_AGE = 900  # 15 minutes
REFRESH_TOKEN_MAX_AGE = 604_800  # 7 days

# Shared parent domain so app / client / admin subdomains all see the
# same auth cookie. Hardcoded by design — no env var knobs.
AUTH_COOKIE_DOMAIN = ".visichek.app"

# Opt-in header: clients that can't rely on cookies (native mobile apps,
# Postman, curl, CLI tools) send this header to receive the tokens in the
# JSON body too. Web browsers should just use the cookies.
INCLUDE_TOKENS_HEADER = "X-Auth-Include-Tokens"
_TRUTHY_HEADER_VALUES = frozenset({"1", "true", "yes", "on"})

# Fields scrubbed from the response body when the caller doesn't opt in.
_TOKEN_FIELDS = ("access_token", "refresh_token")


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    """Set httpOnly auth cookies on the response.

    In production we pin the cookie to ``.visichek.app`` so every subdomain
    sees the same auth. In non-production we omit the ``Domain`` attribute
    entirely — the browser then scopes the cookie to whatever host served
    the request (e.g. ``localhost``), which is what local dev needs.

    Environment is read from ``ENV`` via ``get_settings().is_production`` —
    callers do NOT pass it in. This prevents accidental mismatches between
    set/clear sites and keeps the cookie posture driven solely by .env.
    """
    is_production = get_settings().is_production
    domain = AUTH_COOKIE_DOMAIN if is_production else None
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=REFRESH_TOKEN_MAX_AGE,
        domain=domain,
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
        max_age=REFRESH_TOKEN_MAX_AGE,
        domain=domain,
    )


def tokens_requested_in_body(request: Request) -> bool:
    """True when the caller opted in to receiving tokens in the JSON body.

    Browsers should never set this header — they use the httpOnly cookies
    that ``set_auth_cookies`` installs. The opt-in path exists for clients
    that cannot persist cookies (mobile apps, Postman, curl in scripts).
    """
    raw = request.headers.get(INCLUDE_TOKENS_HEADER, "")
    return raw.strip().lower() in _TRUTHY_HEADER_VALUES


def _extract_token(payload: Any, key: str) -> str:
    if hasattr(payload, key):
        return getattr(payload, key) or ""
    if isinstance(payload, dict):
        return payload.get(key) or ""
    return ""


def _scrub_tokens(body: Any) -> Any:
    """Recursively null ``access_token`` / ``refresh_token`` keys anywhere
    in the response body.

    Recurses into nested dicts and lists because some endpoints return
    tokens one level deeper (e.g. super_admin login returns
    ``{user: {access_token, refresh_token}, tenant: {...}}``). The only
    two keys we touch are the token fields — no other payload values are
    affected.
    """
    if isinstance(body, dict):
        for key in _TOKEN_FIELDS:
            if key in body:
                body[key] = None
        for value in body.values():
            _scrub_tokens(value)
    elif isinstance(body, list):
        for item in body:
            _scrub_tokens(item)
    return body


def _resolve_user_from_payload(payload: Any) -> Any:
    """Return the user-like sub-object from a login payload.

    Most login endpoints return the user object directly. The super-admin
    global login returns ``{"user": {...}, "tenant": {...}}`` — peel the
    wrapper so the session recorder can read ``id``/``role`` uniformly.
    """
    if isinstance(payload, dict) and isinstance(payload.get("user"), (dict, object)):
        return payload["user"]
    return payload


def _payload_field(payload: Any, key: str) -> str:
    """Read a single string field off a model or dict payload.

    Carefully unwraps str-Enum values (``class SystemUserRole(str, Enum)``):
    Python's ``str()`` on a str-Enum returns ``"ClassName.MEMBER"`` (e.g.
    ``"SystemUserRole.SUPER_ADMIN"``), NOT the underlying value
    (``"super_admin"``). Using the formatted string for downstream role
    matching causes ``user_type`` to default to ``"admin"`` for tenant
    users, which is exactly how the system-user sessions list ended up
    empty — the rows existed under the wrong user_type. Always reach
    through ``.value`` for Enum members.
    """
    from enum import Enum

    if isinstance(payload, dict):
        value = payload.get(key)
    else:
        value = getattr(payload, key, None)
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    return str(value) if value else ""


async def _record_session_safe(
    *,
    request: Request,
    payload: Any,
    access_jwt: str,
    previous_access_token_id: str | None = None,
) -> None:
    """Best-effort session capture on every successful login / refresh.

    Decodes the JWT to recover the access-token DB id (the same value
    AuthPrincipal stores as ``access_token_id``) and writes a row to
    the ``sessions`` collection so the Active-Sessions UI has something
    to render. All exceptions are swallowed — recording is observability,
    not a security check, and must never break the login response.
    """
    try:
        user = _resolve_user_from_payload(payload)
        user_id = _payload_field(user, "id") or _payload_field(user, "_id")
        role = _payload_field(user, "role").lower() or "admin"
        if not user_id:
            return

        from security.encrypting_jwt import decode_jwt_token_without_expiration

        decoded = await decode_jwt_token_without_expiration(access_jwt)
        if not decoded or not decoded.get("accessToken"):
            return
        access_token_id = str(decoded["accessToken"])

        # Map role → session.user_type to stay consistent with the
        # /v1/sessions list endpoint, which filters by user_type.
        from security.principal import TENANT_USER_ROLES

        user_type = "system_user" if role in TENANT_USER_ROLES else "admin"

        ip_address = request.client.host if request.client else None
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip() or ip_address
        user_agent = request.headers.get("user-agent")

        from services.session_service import record_session

        await record_session(
            user_id=user_id,
            user_type=user_type,
            access_token_id=access_token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_access_token_id=previous_access_token_id,
        )
    except Exception:
        logger.warning("record_session failed for login response", exc_info=True)


def build_auth_response(
    *,
    request: Request,
    payload: Any,
    message: str,
    status_code: int = 200,
    access_token: str | None = None,
    refresh_token: str | None = None,
    previous_access_token_id: str | None = None,
) -> JSONResponse:
    """Build a login/refresh/otp-verify response in one call.

    Always installs httpOnly auth cookies. The JSON body retains the
    ``access_token`` / ``refresh_token`` fields only when the caller sent
    ``X-Auth-Include-Tokens`` (truthy). Otherwise those two keys are nulled
    in-place — the rest of the payload (profile, permissions, etc.) is
    untouched so existing clients keep working against the non-token fields.

    ``access_token`` / ``refresh_token`` can be passed explicitly; they
    default to whatever ``payload.access_token`` / ``payload.refresh_token``
    resolve to so the common case stays a single call.
    """
    resolved_access = (
        access_token
        if access_token is not None
        else _extract_token(payload, "access_token")
    )
    resolved_refresh = (
        refresh_token
        if refresh_token is not None
        else _extract_token(payload, "refresh_token")
    )
    request_id = getattr(request.state, "request_id", None)

    body = success_payload(
        data=jsonable_encoder(payload),
        message=message,
        request_id=request_id,
    )
    if not tokens_requested_in_body(request):
        _scrub_tokens(body)

    response = JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body, by_alias=False),
    )
    set_auth_cookies(
        response,
        resolved_access or "",
        resolved_refresh or "",
    )

    # Fire-and-forget session recording. We do this after the cookies are
    # set so a failure here can never block the login from succeeding —
    # the user always gets their tokens, the Active-Sessions table catches
    # up a moment later.
    if resolved_access:
        import asyncio

        try:
            asyncio.get_running_loop().create_task(
                _record_session_safe(
                    request=request,
                    payload=payload,
                    access_jwt=resolved_access,
                    previous_access_token_id=previous_access_token_id,
                )
            )
        except RuntimeError:
            # No running loop — happens in some test contexts. Swallow it;
            # the response is still well-formed.
            pass

    return response


def clear_auth_cookies(response: Response) -> None:
    """Delete auth cookies from the response.

    ``domain`` must match the value used at set time — browsers key cookies
    by ``(name, domain, path)``, so deleting without the domain set would
    leave the shared-parent cookie alive. In non-production we never set a
    Domain at write time, so we must not set one here either.

    Environment is read from ``ENV`` via ``get_settings().is_production`` —
    callers do NOT pass it in (see ``set_auth_cookies``).
    """
    is_production = get_settings().is_production
    domain = AUTH_COOKIE_DOMAIN if is_production else None
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        domain=domain,
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
        domain=domain,
    )
