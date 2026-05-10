"""Streaming CSV export helper for read-only list endpoints.

Frontend wants ``Accept: text/csv`` on the same list endpoints with the
same filter params, returning a streaming download. This module
generates RFC-4180-compliant CSV one row at a time and applies the
critical hardening that ad-hoc ``",".join(...)`` always misses:

* **CSV injection (CWE-1236)** — values starting with ``=``, ``+``,
  ``-``, ``@``, tab, or carriage-return are prefixed with ``'`` so
  spreadsheet apps render them as text instead of executing them as a
  formula. This is a hard requirement: a tenant could otherwise
  poison their own audit-log export with ``=cmd|'/c calc'!A1`` and
  any admin opening the file in Excel would execute it.
* **Quote escaping** — values are wrapped in quotes and embedded
  double-quotes are doubled, per RFC 4180.
* **Filename sanitisation** — only alnum / dash / underscore are
  allowed in the suggested filename, with a ``.csv`` extension. Stops
  ``Content-Disposition`` header injection via crafted resource names.

Usage in a route::

    return await csv_response(
        rows=audit_logs_iter,
        columns=["timestamp", "actor_id", "action", ...],
        filename=f"audit-{tenant_id}",
    )
"""

from __future__ import annotations

import io
import re
from typing import Any, AsyncIterator, Iterable, Sequence

from fastapi.responses import StreamingResponse

# Safe filename pattern (no path / quote / control chars).
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Escape ``value`` to a CSV cell, defending against formula injection."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        # Flatten to a semicolon-joined scalar so spreadsheets treat it
        # as a single textual cell.
        s = "; ".join(csv_safe(v) for v in value)
    elif isinstance(value, dict):
        # Stable, terse repr — adjust per resource if a richer rendering
        # is needed.
        s = "; ".join(f"{k}={csv_safe(v)}" for k, v in value.items())
    else:
        s = str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        s = "'" + s
    if any(c in s for c in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def safe_filename(stem: str, *, extension: str = "csv") -> str:
    cleaned = _FILENAME_RE.sub("-", stem).strip("-_.") or "export"
    return f"{cleaned[:80]}.{extension}"


def _format_row(values: Sequence[Any]) -> str:
    return ",".join(csv_safe(v) for v in values) + "\r\n"


async def _async_iter_to_bytes(
    rows: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
    columns: Sequence[str],
) -> AsyncIterator[bytes]:
    yield _format_row(columns).encode("utf-8")
    if hasattr(rows, "__aiter__"):
        async for row in rows:  # type: ignore[union-attr]
            yield _format_row([row.get(c) for c in columns]).encode("utf-8")
    else:
        for row in rows:  # type: ignore[assignment]
            yield _format_row([row.get(c) for c in columns]).encode("utf-8")


def csv_response(
    *,
    rows: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
    columns: Sequence[str],
    filename: str,
) -> StreamingResponse:
    name = safe_filename(filename)
    return StreamingResponse(
        _async_iter_to_bytes(rows, columns),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def csv_buffer(rows: Iterable[Sequence[Any]]) -> bytes:
    """Render rows to an in-memory CSV (use only for small/bounded sets)."""
    buf = io.StringIO()
    for row in rows:
        buf.write(_format_row(row))
    return buf.getvalue().encode("utf-8")


__all__ = ["csv_safe", "csv_response", "csv_buffer", "safe_filename"]
