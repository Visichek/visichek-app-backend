from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from core.response_envelope import success_payload

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
    *,
    is_production: bool = False,
) -> None:
    """Set httpOnly auth cookies on the response."""
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=REFRESH_TOKEN_MAX_AGE,
        domain=AUTH_COOKIE_DOMAIN,
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
        max_age=REFRESH_TOKEN_MAX_AGE,
        domain=AUTH_COOKIE_DOMAIN,
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


def build_auth_response(
    *,
    request: Request,
    payload: Any,
    message: str,
    is_production: bool,
    status_code: int = 200,
    access_token: str | None = None,
    refresh_token: str | None = None,
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
        is_production=is_production,
    )
    return response


def clear_auth_cookies(
    response: Response,
    *,
    is_production: bool = False,
) -> None:
    """Delete auth cookies from the response.

    ``domain`` must match the value used at set time — browsers key cookies
    by ``(name, domain, path)``, so deleting without the domain set would
    leave the shared-parent cookie alive.
    """
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        domain=AUTH_COOKIE_DOMAIN,
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
        domain=AUTH_COOKIE_DOMAIN,
    )
