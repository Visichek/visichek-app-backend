from __future__ import annotations

from fastapi.responses import Response

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"

ACCESS_TOKEN_MAX_AGE = 900        # 15 minutes
REFRESH_TOKEN_MAX_AGE = 604_800   # 7 days


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
        max_age=ACCESS_TOKEN_MAX_AGE,
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
        max_age=REFRESH_TOKEN_MAX_AGE,
    )


def clear_auth_cookies(
    response: Response,
    *,
    is_production: bool = False,
) -> None:
    """Delete auth cookies from the response."""
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/v1/",
    )
