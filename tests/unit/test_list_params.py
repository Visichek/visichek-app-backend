"""Tests for the list-query parser, sort/filter allowlist, and CSV/bulk
helpers introduced for the FE tables overhaul.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from starlette.requests import Request as StarletteRequest
from starlette.types import Scope

from core.bulk import validate_bulk_ids
from core.csv_export import csv_safe, safe_filename
from core.errors import AppException
from core.idempotency import _validate_key, actor_scope
from core.list_params import (
    DEFAULT_LIMIT,
    HARD_LIMIT_CAP,
    FilterDef,
    ListSpec,
    coerce_bool,
    coerce_unix_seconds,
    list_response,
    parse_list_query,
)


def _request(query: str = "") -> Request:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/test",
        "query_string": query.encode("utf-8"),
        "headers": [],
        "app": FastAPI(),
    }
    return StarletteRequest(scope)


SPEC = ListSpec(
    sortable_fields=frozenset({"name", "date_created"}),
    default_sort=(("date_created", -1),),
    search_fields=("name", "email"),
    filters={
        "status": FilterDef(
            name="status", multi=True, allowed_values=frozenset({"active", "inactive"})
        ),
        "active": FilterDef(name="active", coerce=coerce_bool, mongo_field="is_active"),
        "tier": FilterDef(name="tier"),
    },
    range_filters={"createdAt": "date_created"},
    facet_fields=frozenset({"status"}),
)


# ─── ListSpec / parser ───────────────────────────────────────────────


def test_default_pagination():
    q = parse_list_query(_request(""), SPEC)
    assert q.skip == 0
    assert q.limit == DEFAULT_LIMIT
    assert q.sort == [("date_created", -1)]
    assert q.q is None
    assert q.filters == {}


def test_limit_capped():
    q = parse_list_query(_request(f"limit={HARD_LIMIT_CAP + 50}"), SPEC)
    assert q.limit == HARD_LIMIT_CAP


def test_negative_skip_rejected():
    with pytest.raises(AppException) as exc:
        parse_list_query(_request("skip=-1"), SPEC)
    assert exc.value.status_code == 400


def test_sort_allowlist():
    q = parse_list_query(_request("sort=-name,date_created"), SPEC)
    assert q.sort == [("name", -1), ("date_created", 1)]
    assert q.mongo_sort()[-1] == ("_id", -1), "tiebreaker is appended"


def test_sort_field_rejected():
    with pytest.raises(AppException) as exc:
        parse_list_query(_request("sort=password"), SPEC)
    body = exc.value.detail
    assert isinstance(body, dict)
    assert body["details"]["code"] == "INVALID_SORT_FIELD"


def test_unknown_filter_rejected():
    with pytest.raises(AppException) as exc:
        parse_list_query(_request("password=foo"), SPEC)
    body = exc.value.detail
    assert isinstance(body, dict)
    assert body["details"]["code"] == "INVALID_FILTER_FIELD"


def test_filter_allowed_values():
    with pytest.raises(AppException):
        parse_list_query(_request("status=admin"), SPEC)


def test_filter_repeated_keys_uses_in():
    q = parse_list_query(_request("status=active&status=inactive"), SPEC)
    assert q.filters == {"status": {"$in": ["active", "inactive"]}}


def test_bool_coercion():
    q = parse_list_query(_request("active=true"), SPEC)
    assert q.filters == {"is_active": True}


def test_bool_coercion_invalid():
    with pytest.raises(AppException):
        parse_list_query(_request("active=1"), SPEC)


def test_q_too_short_ignored():
    q = parse_list_query(_request("q=a"), SPEC)
    assert q.q is None


def test_q_regex_escaped_in_to_mongo():
    q = parse_list_query(_request("q=foo.bar*"), SPEC)
    mongo = q.to_mongo()
    or_clause = mongo["$and"][0]["$or"]
    # Each search field gets its own clause; check that the regex is escaped.
    fields = {next(iter(clause)): list(clause.values())[0] for clause in or_clause}
    assert "name" in fields and "email" in fields
    for spec in fields.values():
        assert "\\." in spec["$regex"]
        assert "\\*" in spec["$regex"]


def test_range_parses_unix_seconds():
    q = parse_list_query(_request("createdAtGte=1000&createdAtLte=2000"), SPEC)
    assert q.filters == {"date_created": {"$gte": 1000, "$lte": 2000}}


def test_unix_seconds_range_validation():
    with pytest.raises(AppException):
        coerce_unix_seconds("99999999999")  # too large


def test_facet_allowlist():
    with pytest.raises(AppException):
        parse_list_query(_request("facets=email"), SPEC)


def test_facet_accepted():
    q = parse_list_query(_request("facets=status"), SPEC)
    assert q.facets == ["status"]


def test_list_response_envelope():
    payload = list_response([{"a": 1}], total=10, skip=0, limit=5)
    assert payload["meta"]["total"] == 10
    assert payload["meta"]["hasMore"] is True
    assert payload["meta"]["skip"] == 0
    assert payload["meta"]["limit"] == 5


# ─── Bulk validation ────────────────────────────────────────────────


def test_bulk_ids_dedupes_and_validates():
    out = validate_bulk_ids(["64f1a2b3c4d5e6f7a8b9c0d1", "64f1a2b3c4d5e6f7a8b9c0d1"])
    assert out == ["64f1a2b3c4d5e6f7a8b9c0d1"]


def test_bulk_rejects_invalid_objectid():
    with pytest.raises(AppException) as exc:
        validate_bulk_ids(["not-an-objectid"])
    assert exc.value.status_code == 400


def test_bulk_rejects_empty():
    with pytest.raises(AppException):
        validate_bulk_ids([])


def test_bulk_caps_batch():
    ids = [f"64f1a2b3c4d5e6f7a8b9{i:04x}" for i in range(600)]
    with pytest.raises(AppException):
        validate_bulk_ids(ids, max_batch=500)


# ─── CSV safety ─────────────────────────────────────────────────────


def test_csv_safe_escapes_formula_prefix():
    assert csv_safe("=cmd|'/c calc'!A1").startswith("'=")
    assert csv_safe("+cmd").startswith("'+")
    assert csv_safe("-cmd").startswith("'-")
    assert csv_safe("@cmd").startswith("'@")


def test_csv_safe_quotes_embedded_quotes():
    assert csv_safe('hello "world"') == '"hello ""world"""'


def test_safe_filename_strips_path_chars():
    assert safe_filename("../etc/passwd").endswith(".csv")
    assert "/" not in safe_filename("../etc/passwd")
    assert ".." not in safe_filename("../etc/passwd")


# ─── Idempotency-key validation ──────────────────────────────────────


def test_idempotency_key_format():
    assert _validate_key("abcd1234") == "abcd1234"
    with pytest.raises(AppException):
        _validate_key("short")
    with pytest.raises(AppException):
        _validate_key("has spaces in it")


def test_actor_scope_includes_role():
    assert actor_scope("u1", "admin") == "admin:u1"
    assert actor_scope(None, "admin") == ""
