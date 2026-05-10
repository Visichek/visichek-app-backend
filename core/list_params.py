"""Shared list-query parsing for table endpoints.

Implements the cross-cutting contract from the frontend tables spec
(``backend-docs/tables-spec.txt``):

* ``skip`` / ``limit`` pagination with a documented hard cap.
* ``sort=field,-field`` parsing against a per-resource allowlist.
* ``q`` free-text search against a per-resource field allowlist.
* Repeated-key filter parsing (``?status=a&status=b``) plus single-value
  and CSV forms, with explicit type coercion (bool / int / unix-epoch).
* Optional ``facets`` request that returns count breakdowns over the
  full filter set, not the current page.

Security notes:

* Sort fields, search fields, filter fields, and facet fields are all
  validated against a per-resource allowlist defined by ``ListSpec``.
  Anything not in the allowlist returns ``400 INVALID_*_FIELD`` instead
  of being silently passed through. This prevents callers from probing
  arbitrary Mongo paths or facet-counting on PII columns.
* ``q`` is escaped with :func:`re.escape` before being wrapped in a
  case-insensitive Mongo regex. Pure raw input would let callers DoS
  the query planner with pathological patterns or smuggle regex
  metacharacters into the filter.
* Numeric / bool params reject anything that doesn't parse exactly
  (``"true"``/``"false"``, integer literal, float literal). No silent
  truthiness — the spec is explicit and the parser enforces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from fastapi import Request

from core.case_conversion import _to_snake
from core.errors import AppException, ErrorCode

DEFAULT_LIMIT = 25
HARD_LIMIT_CAP = 200
DEFAULT_SEARCH_MIN_LEN = 2
DEFAULT_SORT_TIEBREAKER = ("_id", -1)

# Reserved query keys that the parser consumes itself; per-resource
# filter allowlists must not collide with these names.
_RESERVED_KEYS = frozenset({"skip", "limit", "sort", "q", "facets"})


def _bad_param(field_name: str, message: str) -> AppException:
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message=message,
        details={"field": field_name},
    )


def _invalid_sort(field_name: str) -> AppException:
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Invalid sort field",
        details={"code": "INVALID_SORT_FIELD", "field": field_name},
    )


def _invalid_filter(field_name: str) -> AppException:
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Invalid filter field",
        details={"code": "INVALID_FILTER_FIELD", "field": field_name},
    )


def _invalid_facet(field_name: str) -> AppException:
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Invalid facet field",
        details={"code": "INVALID_FACET_FIELD", "field": field_name},
    )


# ─── FilterDef ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FilterDef:
    """Per-filter rules for parsing & translating into a Mongo expression.

    ``mongo_field`` lets the public query name differ from the document
    path (e.g. ``planTier`` → ``plan_tier``). ``allowed_values`` blocks
    callers from injecting arbitrary values into enum-like fields.
    ``coerce`` is a callable that converts the parsed string into the
    Mongo value (e.g. unix-epoch ints, booleans, ObjectId checks).
    """

    name: str
    mongo_field: Optional[str] = None
    multi: bool = False
    coerce: Optional[Callable[[str], Any]] = None
    allowed_values: Optional[frozenset[str]] = None
    # Special handling: the parser builds a complete fragment instead of
    # the default ``{field: value}`` / ``{field: {$in: [...]}}``.
    builder: Optional[Callable[[Sequence[str]], Mapping[str, Any]]] = None

    def field_path(self) -> str:
        return self.mongo_field or self.name


def coerce_bool(raw: str) -> bool:
    s = raw.strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    raise _bad_param("filter", f"Expected 'true' or 'false', got {raw!r}")


def coerce_int(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise _bad_param("filter", f"Expected integer, got {raw!r}")


def coerce_unix_seconds(raw: str) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise _bad_param("filter", f"Expected unix timestamp seconds, got {raw!r}")
    # Sanity bounds: epoch 0 .. year 2100. Wider ranges hint at ms vs s
    # confusion or attempted overflow tricks against the query planner.
    if v < 0 or v > 4_102_444_800:
        raise _bad_param("filter", "Timestamp out of range")
    return v


# ─── ListSpec ─────────────────────────────────────────────────────────


@dataclass
class ListSpec:
    """Per-resource declarative parsing rules.

    Defined once per route module (e.g. ``TENANTS_LIST_SPEC``) and
    handed to :func:`parse_list_query` so the route stays declarative
    instead of repeating ``.split(",")`` plumbing.
    """

    sortable_fields: frozenset[str] = field(default_factory=frozenset)
    default_sort: tuple[tuple[str, int], ...] = (("date_created", -1),)
    search_fields: tuple[str, ...] = ()
    search_min_len: int = DEFAULT_SEARCH_MIN_LEN
    filters: dict[str, FilterDef] = field(default_factory=dict)
    facet_fields: frozenset[str] = field(default_factory=frozenset)
    default_limit: int = DEFAULT_LIMIT
    max_limit: int = HARD_LIMIT_CAP
    # Range filter pairs: ``"createdAt"`` → fields
    # ``createdAtGte`` / ``createdAtLte`` mapping to ``date_created``.
    range_filters: dict[str, str] = field(default_factory=dict)


# ─── ListQuery (parsed result) ────────────────────────────────────────


@dataclass
class ListQuery:
    skip: int
    limit: int
    sort: list[tuple[str, int]]
    q: Optional[str]
    filters: dict[str, Any]
    facets: list[str]
    spec: ListSpec

    def to_mongo(self, base_filter: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Compose the parsed filters + q into a Mongo filter document.

        ``base_filter`` is merged in last so the caller can lock in
        tenant scoping that the public query string can't override.
        """
        out: dict[str, Any] = {}
        for key, value in self.filters.items():
            if isinstance(value, dict) and key in out and isinstance(out[key], dict):
                out[key] = {**out[key], **value}
            else:
                out[key] = value

        if self.q and self.spec.search_fields:
            pattern = re.escape(self.q)
            ors = [
                {field_name: {"$regex": pattern, "$options": "i"}}
                for field_name in self.spec.search_fields
            ]
            if ors:
                out.setdefault("$and", []).append({"$or": ors})

        if base_filter:
            for key, value in base_filter.items():
                if key in out:
                    out.setdefault("$and", []).append({key: value})
                else:
                    out[key] = value
        return out

    def mongo_sort(self) -> list[tuple[str, int]]:
        """Return the sort spec including the stable-id tiebreaker."""
        sort_spec = list(self.sort)
        if not any(field_name == DEFAULT_SORT_TIEBREAKER[0] for field_name, _ in sort_spec):
            sort_spec.append(DEFAULT_SORT_TIEBREAKER)
        return sort_spec


# ─── Parsing ──────────────────────────────────────────────────────────


def _parse_int(raw: Any, field_name: str, *, minimum: int = 0) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise _bad_param(field_name, f"Expected integer for {field_name!r}")
    if v < minimum:
        raise _bad_param(field_name, f"{field_name} must be >= {minimum}")
    return v


def _resolve_allowlisted_field(field_name: str, allowlist: frozenset[str]) -> Optional[str]:
    """Match ``field_name`` against ``allowlist`` accepting either case form.

    Public query params follow the camelCase convention enforced for bodies
    by ``CaseConversionMiddleware``, but list-spec allowlists are declared
    in snake_case to match the underlying Mongo field paths. Try the literal
    first (covers single-word names and explicitly camelCased filter keys),
    then fall back to the snake_case translation so ``dateCreated`` resolves
    to ``date_created`` without a 400.
    """
    if field_name in allowlist:
        return field_name
    snake = _to_snake(field_name)
    if snake != field_name and snake in allowlist:
        return snake
    return None


def _parse_sort(raw: Optional[str], spec: ListSpec) -> list[tuple[str, int]]:
    if not raw:
        return list(spec.default_sort)

    out: list[tuple[str, int]] = []
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        if token.startswith("-"):
            field_name = token[1:]
            direction = -1
        else:
            field_name = token
            direction = 1
        if not field_name:
            raise _invalid_sort(token)
        resolved = _resolve_allowlisted_field(field_name, spec.sortable_fields)
        if resolved is None:
            raise _invalid_sort(field_name)
        out.append((resolved, direction))
    if not out:
        return list(spec.default_sort)
    return out


def _gather_multi(request: Request, key: str) -> list[str]:
    """Read a query param as ``[a, b]`` from either repeated keys or
    a single CSV value. We accept both because clients vary, but the
    spec recommends repeated-key form.
    """
    values: list[str] = []
    raw_list = request.query_params.getlist(key)
    for raw in raw_list:
        if raw is None:
            continue
        for chunk in raw.split(","):
            cleaned = chunk.strip()
            if cleaned:
                values.append(cleaned)
    return values


def _apply_filter(
    out: dict[str, Any],
    fdef: FilterDef,
    raw_values: list[str],
) -> None:
    if not raw_values:
        return

    if fdef.allowed_values:
        for v in raw_values:
            if v not in fdef.allowed_values:
                raise _bad_param(
                    fdef.name,
                    f"Filter {fdef.name!r} got disallowed value {v!r}",
                )

    if fdef.builder is not None:
        fragment = fdef.builder(raw_values)
        for k, v in fragment.items():
            out[k] = v
        return

    coerced: list[Any]
    if fdef.coerce:
        coerced = [fdef.coerce(v) for v in raw_values]
    else:
        coerced = list(raw_values)

    target = fdef.field_path()
    if fdef.multi and len(coerced) > 1:
        out[target] = {"$in": coerced}
    else:
        out[target] = coerced[0]


def _parse_range_filters(
    request: Request,
    spec: ListSpec,
    out: dict[str, Any],
) -> None:
    """Parse ``<base>Gte`` / ``<base>Lte`` pairs declared in ``spec.range_filters``.

    Each base maps to a Mongo field; values are unix-epoch seconds and
    pass through ``coerce_unix_seconds`` for safety.
    """
    for base, mongo_field in spec.range_filters.items():
        gte_param = f"{base}Gte"
        lte_param = f"{base}Lte"
        gte_raw = request.query_params.get(gte_param)
        lte_raw = request.query_params.get(lte_param)
        if gte_raw is None and lte_raw is None:
            continue
        fragment: dict[str, Any] = {}
        if gte_raw is not None:
            fragment["$gte"] = coerce_unix_seconds(gte_raw)
        if lte_raw is not None:
            fragment["$lte"] = coerce_unix_seconds(lte_raw)
        existing = out.get(mongo_field)
        if isinstance(existing, dict):
            existing.update(fragment)
        else:
            out[mongo_field] = fragment


def parse_list_query(request: Request, spec: ListSpec) -> ListQuery:
    """Parse the request's query params against ``spec``.

    Raises ``AppException`` (400) on any disallowed sort/filter/facet
    field, malformed integer/timestamp/bool, or invalid skip/limit.
    """
    skip_raw = request.query_params.get("skip", "0")
    limit_raw = request.query_params.get("limit", str(spec.default_limit))
    skip = _parse_int(skip_raw, "skip", minimum=0)
    limit = _parse_int(limit_raw, "limit", minimum=1)
    if limit > spec.max_limit:
        limit = spec.max_limit

    sort = _parse_sort(request.query_params.get("sort"), spec)

    q_raw = request.query_params.get("q")
    q: Optional[str] = None
    if q_raw is not None:
        cleaned = q_raw.strip()
        if len(cleaned) >= spec.search_min_len:
            q = cleaned

    filters: dict[str, Any] = {}

    # Reject filter param keys that aren't in the allowlist (or in the
    # reserved set or recognised range params). This is the security
    # boundary that prevents callers from probing arbitrary Mongo paths.
    range_param_names: set[str] = set()
    for base in spec.range_filters:
        range_param_names.add(f"{base}Gte")
        range_param_names.add(f"{base}Lte")

    for key in request.query_params.keys():
        if key in _RESERVED_KEYS:
            continue
        if key in range_param_names:
            continue
        if key in spec.filters:
            continue
        raise _invalid_filter(key)

    for fdef in spec.filters.values():
        raw_values = _gather_multi(request, fdef.name)
        _apply_filter(filters, fdef, raw_values)

    _parse_range_filters(request, spec, filters)

    facets_raw = request.query_params.get("facets")
    facets: list[str] = []
    if facets_raw:
        for chunk in facets_raw.split(","):
            cleaned = chunk.strip()
            if not cleaned:
                continue
            resolved_facet = _resolve_allowlisted_field(cleaned, spec.facet_fields)
            if resolved_facet is None:
                raise _invalid_facet(cleaned)
            facets.append(resolved_facet)

    return ListQuery(
        skip=skip,
        limit=limit,
        sort=sort,
        q=q,
        filters=filters,
        facets=facets,
        spec=spec,
    )


# ─── Response shape ───────────────────────────────────────────────────


def list_response(
    items: Sequence[Any],
    *,
    total: int,
    skip: int,
    limit: int,
    facets: Optional[Mapping[str, Mapping[str, int]]] = None,
    has_more: Optional[bool] = None,
) -> dict[str, Any]:
    """Build the standard list response payload for ``@document_response(include_meta=True)``.

    Returns the ``{"items": ..., "meta": {...}}`` dict the response
    envelope decoder understands. Meta carries ``total`` (omitted when
    set explicitly to None — for very-hot endpoints that can't afford
    a count query), ``skip``, ``limit``, ``hasMore``, and optional
    ``facets`` breakdown.
    """
    payload_items = list(items)
    computed_has_more = (
        has_more
        if has_more is not None
        else (skip + len(payload_items) < total if total is not None else False)
    )
    meta: dict[str, Any] = {
        "skip": skip,
        "limit": limit,
        "hasMore": computed_has_more,
    }
    if total is not None:
        meta["total"] = total
    if facets:
        meta["facets"] = {k: dict(v) for k, v in facets.items()}
    return {"items": payload_items, "meta": meta}


__all__ = [
    "DEFAULT_LIMIT",
    "HARD_LIMIT_CAP",
    "FilterDef",
    "ListSpec",
    "ListQuery",
    "coerce_bool",
    "coerce_int",
    "coerce_unix_seconds",
    "parse_list_query",
    "list_response",
]
