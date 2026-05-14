"""Unit tests for the auth-cookie helper."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.responses import Response

from security.cookie_utils import (
    ACCESS_TOKEN_COOKIE,
    AUTH_COOKIE_DOMAIN,
    REFRESH_TOKEN_COOKIE,
    build_auth_response,
    clear_auth_cookies,
    set_auth_cookies,
    tokens_requested_in_body,
)


@contextmanager
def _env(is_production: bool):
    """Patch ``get_settings().is_production`` for the cookie helpers.

    ``is_production`` is no longer a parameter — the helpers read it from
    ``core.settings.get_settings()`` so we patch the source.
    """
    fake = MagicMock()
    fake.is_production = is_production
    with patch("security.cookie_utils.get_settings", return_value=fake):
        yield


def _fake_request(headers: dict | None = None):
    r = MagicMock()
    r.headers = headers or {}
    r.state = MagicMock(request_id="req-1")
    return r


# ---------------------------------------------------------------------------
# Cookie attributes
# ---------------------------------------------------------------------------


def test_set_auth_cookies_emits_visichek_domain() -> None:
    assert AUTH_COOKIE_DOMAIN == ".visichek.app"
    resp = Response()
    with _env(is_production=True):
        set_auth_cookies(resp, "at", "rt")

    raw = [v.decode() for k, v in resp.headers.raw if k == b"set-cookie"]
    assert len(raw) == 2
    assert all("domain=.visichek.app" in v.lower() for v in raw)
    # Production → Secure flag set.
    assert all("secure" in v.lower() for v in raw)
    # Never JavaScript-visible.
    assert all("httponly" in v.lower() for v in raw)
    # SameSite=Lax so top-level nav from other .visichek.app subdomains
    # still sends the cookie.
    assert all("samesite=lax" in v.lower() for v in raw)
    # Both cookies present.
    joined = " ".join(raw)
    assert ACCESS_TOKEN_COOKIE in joined
    assert REFRESH_TOKEN_COOKIE in joined


def test_set_auth_cookies_omits_secure_in_non_production() -> None:
    resp = Response()
    with _env(is_production=False):
        set_auth_cookies(resp, "at", "rt")
    raw = [v.decode() for k, v in resp.headers.raw if k == b"set-cookie"]
    # Non-production: no Domain attribute — cookie is scoped to the
    # request host (e.g. localhost) so local dev works.
    assert all("domain=" not in v.lower() for v in raw)
    assert all("secure" not in v.lower() for v in raw)


def test_clear_auth_cookies_omits_domain_in_non_production() -> None:
    resp = Response()
    with _env(is_production=False):
        clear_auth_cookies(resp)
    raw = [v.decode() for k, v in resp.headers.raw if k == b"set-cookie"]
    assert len(raw) == 2
    assert all("domain=" not in v.lower() for v in raw)
    assert all("max-age=0" in v.lower() for v in raw)


def test_clear_auth_cookies_matches_set_domain() -> None:
    # Browsers key cookies by (name, domain, path). A clear without the
    # matching Domain attribute leaves the parent-domain cookie alive —
    # this test pins the helper against that regression.
    resp = Response()
    with _env(is_production=True):
        clear_auth_cookies(resp)

    raw = [v.decode() for k, v in resp.headers.raw if k == b"set-cookie"]
    assert len(raw) == 2
    assert all("domain=.visichek.app" in v.lower() for v in raw)
    # Delete means Max-Age=0.
    assert all("max-age=0" in v.lower() for v in raw)


# ---------------------------------------------------------------------------
# Opt-in header tests
# ---------------------------------------------------------------------------


def test_tokens_requested_in_body_truthy_values() -> None:
    for val in ("1", "true", "TRUE", "yes", "on", "  1  "):
        assert tokens_requested_in_body(_fake_request({"X-Auth-Include-Tokens": val}))


def test_tokens_requested_in_body_falsy_values() -> None:
    for val in ("", "0", "false", "no", "nope"):
        assert not tokens_requested_in_body(
            _fake_request({"X-Auth-Include-Tokens": val})
        )
    assert not tokens_requested_in_body(_fake_request({}))


def _build_auth_body(request_headers: dict | None, payload) -> dict:
    with _env(is_production=False):
        resp = build_auth_response(
            request=_fake_request(request_headers),
            payload=payload,
            message="ok",
        )
    return json.loads(bytes(resp.body))


def test_build_auth_response_strips_tokens_by_default() -> None:
    """Default path — no opt-in header — tokens are null in the body."""
    body = _build_auth_body(
        None,
        {
            "id": "u1",
            "email": "a@b.com",
            "access_token": "ACCESS_ABC",
            "refresh_token": "REFRESH_XYZ",
        },
    )
    assert body["success"] is True
    assert body["data"]["id"] == "u1"
    assert body["data"]["email"] == "a@b.com"
    # Tokens are null in the body.
    assert body["data"]["access_token"] is None
    assert body["data"]["refresh_token"] is None


def test_build_auth_response_includes_tokens_when_header_set() -> None:
    body = _build_auth_body(
        {"X-Auth-Include-Tokens": "1"},
        {
            "id": "u1",
            "access_token": "ACCESS_ABC",
            "refresh_token": "REFRESH_XYZ",
        },
    )
    assert body["data"]["access_token"] == "ACCESS_ABC"
    assert body["data"]["refresh_token"] == "REFRESH_XYZ"


def test_build_auth_response_scrubs_nested_tokens() -> None:
    """Super_admin login shape — tokens live one level down under ``user``."""
    body = _build_auth_body(
        None,
        {
            "user": {
                "id": "u1",
                "access_token": "NESTED_ACCESS",
                "refresh_token": "NESTED_REFRESH",
            },
            "tenant": {"id": "t1", "company_name": "Acme"},
            "tenant_login_url": "/v1/system-users/tenant/t1/login",
        },
    )
    assert body["data"]["user"]["access_token"] is None
    assert body["data"]["user"]["refresh_token"] is None
    # Non-token data untouched.
    assert body["data"]["user"]["id"] == "u1"
    assert body["data"]["tenant"]["company_name"] == "Acme"
    assert body["data"]["tenant_login_url"] == "/v1/system-users/tenant/t1/login"


def test_build_auth_response_always_sets_cookies() -> None:
    """Cookies MUST be set regardless of the opt-in header — that's the whole
    point of the shift (cookies are the primary auth channel)."""
    for headers in (None, {"X-Auth-Include-Tokens": "1"}):
        with _env(is_production=True):
            resp = build_auth_response(
                request=_fake_request(headers),
                payload={
                    "access_token": "A",
                    "refresh_token": "R",
                },
                message="ok",
            )
        raw = [v.decode() for k, v in resp.headers.raw if k == b"set-cookie"]
        joined = " ".join(raw)
        assert "access_token=A" in joined
        assert "refresh_token=R" in joined
        assert "domain=.visichek.app" in joined.lower()
