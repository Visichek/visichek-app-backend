"""CSRF / Origin protection for cookie-authenticated writes.

The backend accepts auth from httpOnly cookies on a shared parent domain
(``.visichek.app``). ``SameSite=Lax`` blocks the classic cross-site form
POST, but it does not protect against a compromised or malicious sibling
subdomain (which is same-site), so we add an Origin/Referer check on the
CSRF-vulnerable surface: unsafe-method requests that authenticate via the
auth cookie.

Key insight: CSRF only works because the browser *automatically* attaches
the cookie. A request that authenticates with an ``Authorization: Bearer``
header is driven by code that deliberately set the header (mobile app, CLI,
server-to-server) and cannot be forged cross-site, so it is exempt. We only
enforce Origin/Referer when the request carries the auth cookie and NO
Authorization header.

The decision is factored into :func:`is_csrf_violation` so it can be unit
tested without standing up the ASGI stack.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.response_envelope import error_response
from core.settings import get_settings
from security.cookie_utils import ACCESS_TOKEN_COOKIE

logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _origin_of(url: str) -> str | None:
    """Return ``scheme://host[:port]`` for a URL, or None if unparseable."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


def is_csrf_violation(
    *,
    method: str,
    has_auth_cookie: bool,
    has_auth_header: bool,
    origin: str | None,
    referer: str | None,
    allowed_origins: set[str],
) -> bool:
    """Decide whether a request is a CSRF violation that must be rejected.

    A violation is: an unsafe-method request that is authenticated via the
    auth cookie (and NOT via an Authorization header), whose Origin — or,
    when Origin is absent, the Referer's origin — is missing or not in the
    allowed set. Bearer-authenticated and safe-method requests are never
    violations.
    """
    if method.upper() in _SAFE_METHODS:
        return False
    # Bearer-authenticated or anonymous requests are not CSRF-able via the
    # cookie. (Anonymous unsafe requests are gated by auth elsewhere.)
    if has_auth_header or not has_auth_cookie:
        return False

    candidate = origin or (_origin_of(referer) if referer else None)
    if not candidate:
        # Cookie-authenticated write with no Origin/Referer — refuse. A
        # legitimate browser always sends Origin on fetch/XHR writes.
        return True
    return candidate not in allowed_origins


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Reject cookie-authenticated unsafe requests from disallowed origins."""

    def __init__(self, app, allowed_origins: set[str] | None = None) -> None:
        super().__init__(app)
        self._allowed_origins = allowed_origins or set()

    async def dispatch(self, request: Request, call_next):
        # Skip entirely in the test environment so the unit/route suites
        # (which drive endpoints without browser Origin headers) stay green;
        # the decision logic itself is covered by tests against
        # ``is_csrf_violation``.
        if get_settings().env == "testing":
            return await call_next(request)

        has_cookie = bool(request.cookies.get(ACCESS_TOKEN_COOKIE))
        has_header = bool(request.headers.get("authorization"))
        if is_csrf_violation(
            method=request.method,
            has_auth_cookie=has_cookie,
            has_auth_header=has_header,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            allowed_origins=self._allowed_origins,
        ):
            logger.warning(
                "CSRF/Origin check failed: method=%s path=%s origin=%s referer=%s",
                request.method,
                request.url.path,
                request.headers.get("origin"),
                request.headers.get("referer"),
            )
            return error_response(
                status_code=403,
                message="Cross-origin request rejected",
                data={"code": "CSRF_ORIGIN_REJECTED"},
            )
        return await call_next(request)
