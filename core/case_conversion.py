"""
Case Conversion Middleware
==========================

Automatically converts JSON response keys between snake_case and camelCase.

Default: camelCase responses (most frontend frameworks expect this).
Override: Send header  X-Response-Case: snake  to get snake_case responses.

Request bodies are always accepted in BOTH formats — the middleware normalises
incoming camelCase keys to snake_case before they reach Pydantic validators.

Usage in main.py:
    from core.case_conversion import CaseConversionMiddleware
    app.add_middleware(CaseConversionMiddleware)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"([A-Z]+)")
_SNAKE_RE = re.compile(r"_([a-z0-9])")


def _to_camel(name: str) -> str:
    """Convert snake_case → camelCase.

    Leading underscores are stripped so MongoDB-style ``_id`` fields collapse
    to ``id`` uniformly, matching how Pydantic models serialise with
    ``by_alias=False``. Without this, precompute/cache paths that dump with
    ``by_alias=True`` would surface ``Id`` to clients while direct responses
    surface ``id``.
    """
    if "_" not in name:
        return name
    stripped = name.lstrip("_")
    if "_" not in stripped:
        return stripped
    parts = stripped.split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:] if w)


def _to_snake(name: str) -> str:
    """Convert camelCase → snake_case."""
    result = _CAMEL_RE.sub(r"_\1", name)
    return result.lower().lstrip("_")


def _convert_keys(obj: Any, converter: Any) -> Any:
    """Recursively convert all dict keys using *converter*."""
    if isinstance(obj, dict):
        return {converter(k): _convert_keys(v, converter) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_keys(item, converter) for item in obj]
    return obj


# ── middleware ────────────────────────────────────────────────────────


def _is_signed_webhook(path: str, method: str) -> bool:
    """Return ``True`` for POSTs whose raw body is HMAC-signed by the
    sender (Dojah KYC, Stripe / Flutterwave payments).

    The case conversion middleware MUST NOT rewrite these bodies — the
    handler computes an HMAC over the raw bytes and compares it to the
    sender's signature header. Re-serialising the JSON would change the
    byte sequence (key order, whitespace, snake-case rewrite) and the
    signature would fail every time.
    """
    if method != "POST":
        return False
    if path == "/v1/kyc/webhook":
        return True
    # ``POST /v1/payments/webhooks/{provider}`` — single trailing
    # segment. The admin-facing replay endpoint
    # (``/v1/payments/webhooks/replay/{event_id}``) has two segments
    # and is fine to convert; the events list is a GET so it never
    # reaches this branch.
    prefix = "/v1/payments/webhooks/"
    if path.startswith(prefix):
        suffix = path[len(prefix) :]
        if suffix and "/" not in suffix:
            return True
    return False


class CaseConversionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Normalises incoming JSON request bodies from camelCase → snake_case
       so Pydantic models always receive snake_case keys.
    2. Converts outgoing JSON response bodies to camelCase (default) or
       snake_case if the client sends  X-Response-Case: snake.

    Signed-webhook receivers (Dojah KYC, Stripe / Flutterwave payments)
    are exempt from the inbound rewrite — those handlers verify an HMAC
    over the raw request bytes and any mutation breaks verification.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # ── Normalise inbound body ───────────────────────────────────
        content_type = request.headers.get("content-type", "")
        path = request.url.path
        if "application/json" in content_type and request.method in (
            "POST",
            "PUT",
            "PATCH",
        ):
            signed = _is_signed_webhook(path, request.method)
            # Trace the bypass decision for any path that smells like a
            # webhook so we can confirm signed receivers stay verbatim.
            if signed or "webhook" in path.lower() or "/kyc/" in path:
                logger.info(
                    "case_conversion: path=%s method=%s signed_webhook=%s "
                    "body_will_be_rewritten=%s",
                    path,
                    request.method,
                    signed,
                    not signed,
                )
            if not signed:
                body = await request.body()
                if body:
                    try:
                        parsed = json.loads(body)
                        normalised = _convert_keys(parsed, _to_snake)
                        encoded = json.dumps(normalised).encode("utf-8")
                        # Swap the receive channel so downstream reads
                        # our modified body.
                        request._body = encoded  # type: ignore[attr-defined]
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass  # Let FastAPI's own validation surface the error

        # ── Determine desired response casing ────────────────────────
        prefer = request.headers.get("X-Response-Case", "camel").strip().lower()
        use_camel = prefer != "snake"

        # ── Call downstream ──────────────────────────────────────────
        response = await call_next(request)

        # Only transform JSON responses
        resp_ct = response.headers.get("content-type", "")
        if "application/json" not in resp_ct:
            return response

        # Read the full body from the streaming response
        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[union-attr]
            if isinstance(chunk, str):
                body_chunks.append(chunk.encode("utf-8"))
            else:
                body_chunks.append(chunk)
        raw_body = b"".join(body_chunks)

        if not raw_body:
            return response

        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                content=raw_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        converter = _to_camel if use_camel else _to_snake
        converted = _convert_keys(data, converter)
        new_body = json.dumps(converted, ensure_ascii=False).encode("utf-8")

        # Collect headers, but skip content-length (we recalculate) and
        # set-cookie (added back separately to preserve multi-value).
        resp_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "set-cookie")
        }
        resp_headers["content-length"] = str(len(new_body))

        new_response = Response(
            content=new_body,
            status_code=response.status_code,
            headers=resp_headers,
            media_type="application/json",
        )

        # Re-attach every Set-Cookie header (multi-value safe)
        for k, v in response.headers.raw:
            if k.lower() == b"set-cookie":
                new_response.headers.append("set-cookie", v.decode("latin-1"))

        return new_response
