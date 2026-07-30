"""Boot-time drift check: every ``ListSpec`` field name must exist on its schema.

``ListSpec`` allowlists (sortable / search / filter / facet / range fields) are
hand-written strings that Mongo will happily accept even when no document has
the path. The failure is silent and user-visible in the worst way:

  * a phantom **sort** field sorts by a missing key → arbitrary row order;
  * a phantom **search** field drops that column from ``?q=`` → matches nothing;
  * a phantom **filter** field builds ``{"nope": "value"}`` → **zero rows**,
    indistinguishable from "no results" in the UI.

Both known drift bugs (support-case ``sla_deadline`` / ``title,summary,
case_number``, and the eight found in the 2026-07 sweep — DSR ``type``, invoice
``amount``, subscription ``renews_at`` …) are exactly this shape and would all
have been caught here.

Design mirrors ``assert_admin_permission_coverage``: **log at boot, never
crash**, and let a unit test assert the findings list is empty so CI fails hard
while a running deployment does not. Findings are data, not exceptions.

Specs are discovered by scanning loaded ``api.v1.*`` modules for ``ListSpec``
instances, so a new spec is covered the moment its route module is imported —
there is nothing to register and nothing to forget.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from core.list_params import ListSpec

logger = logging.getLogger(__name__)

# Always-valid paths: Mongo's document id plus the aliases the *Out schemas
# map it to. Timestamps are declared on the schemas themselves.
_ALWAYS_VALID = frozenset({"_id", "id"})


@dataclass(frozen=True)
class SpecFinding:
    """One phantom field on one spec."""

    spec: str
    kind: str  # sort | default_sort | search | filter | facet | range
    field: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.spec}: {self.kind} field {self.field!r} is not on the schema"


def _model_field_names(model: type) -> frozenset[str]:
    """Every name a document may legitimately use for a model field.

    Covers the declared attribute name AND the serialisation/validation
    aliases, because the *Out schemas map ``id`` ↔ ``_id`` via ``alias``.
    """
    names: set[str] = set()
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, dict):
        return frozenset()
    for name, info in fields.items():
        names.add(name)
        for attr in ("alias", "validation_alias", "serialization_alias"):
            alias = getattr(info, attr, None)
            if isinstance(alias, str):
                names.add(alias)
    return frozenset(names)


def _root(path: str) -> str:
    """First segment of a dotted Mongo path (``a.b.c`` → ``a``).

    Nested paths are only checked to their root: the schema knows the
    top-level field exists, and validating into sub-models is more precision
    than the phantom-field class of bug needs.
    """
    return path.split(".", 1)[0]


def _check_spec(label: str, spec: ListSpec) -> list[SpecFinding]:
    model = spec.model
    if model is None:
        return []
    valid = _model_field_names(model) | _ALWAYS_VALID | spec.extra_fields
    if not valid:
        return []

    findings: list[SpecFinding] = []

    def check(kind: str, path: str) -> None:
        if _root(path) not in valid:
            findings.append(SpecFinding(spec=label, kind=kind, field=path))

    for name in sorted(spec.sortable_fields):
        check("sort", name)
    for name, _direction in spec.default_sort:
        check("default_sort", name)
    for name in spec.search_fields:
        check("search", name)
    # facet_fields are deliberately NOT checked: a facet name is interpreted
    # by the route's `facet_runner` (run_list takes it as an argument), so it
    # is a runner-defined label rather than a document path. `tenants` facets
    # on "status" while the document stores `is_active`, and system-users
    # facets on "accountStatus" — both correct, both would false-positive.
    for name in spec.range_filters.values():
        check("range", name)

    for filter_def in spec.filters.values():
        # A builder composes its own fragment, so the public filter name is
        # not a document path and the real paths can't be read statically.
        # An `external` filter is resolved by the route via a join and popped
        # before run_list. Everything else queries `field_path()` directly —
        # which is precisely where a phantom name silently returns zero rows.
        if filter_def.builder is not None or filter_def.external:
            continue
        check("filter", filter_def.field_path())

    return findings


def iter_list_specs() -> Iterator[tuple[str, ListSpec]]:
    """Yield ``(label, spec)`` for every ListSpec on a loaded api.v1 module.

    Relies on ``main.py`` having imported the route modules, which is true at
    boot and in any test that imports the app.
    """
    for module_name in sorted(sys.modules):
        if not module_name.startswith("api.v1"):
            continue
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr_name, value in vars(module).items():
            if isinstance(value, ListSpec):
                yield f"{module_name}.{attr_name}", value


def validate_list_specs() -> list[SpecFinding]:
    """Return every phantom field across all discovered specs."""
    findings: list[SpecFinding] = []
    for label, spec in iter_list_specs():
        findings.extend(_check_spec(label, spec))
    return findings


def unvalidated_list_specs() -> list[str]:
    """Labels of specs with no ``model`` set — invisible to the check."""
    return [label for label, spec in iter_list_specs() if spec.model is None]


def assert_list_spec_coverage(app: Optional[Any] = None) -> list[SpecFinding]:
    """Boot hook: log drift + unvalidated specs. Never raises.

    ``app`` is accepted (and unused) so this can sit alongside the other
    startup assertions in ``main.py`` with a uniform call shape.
    """
    _ = app
    findings = validate_list_specs()
    if findings:
        logger.error(
            "ListSpec drift: %d field(s) reference paths absent from their schema "
            "— these produce silent zero-result filters / arbitrary sort order. %s",
            len(findings),
            "; ".join(str(f) for f in findings),
        )
    unvalidated = unvalidated_list_specs()
    if unvalidated:
        logger.info(
            "ListSpec drift check skipped %d spec(s) with no model set: %s",
            len(unvalidated),
            ", ".join(unvalidated),
        )
    return findings
