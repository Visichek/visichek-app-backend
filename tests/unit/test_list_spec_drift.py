"""CI guard for ListSpec/schema drift.

``core.list_spec_validation`` only LOGS at boot (a running deployment must not
crash over a filter name). This test is the half that bites: it fails the build
when a spec names a field its schema doesn't have.

When this test fails, the fix is in the route's ``ListSpec`` — either the field
name is wrong (use the real schema field) or the filter is route-resolved /
builder-composed and should say so via ``external=True`` / ``builder=``.
Do NOT silence it by adding the phantom name to ``extra_fields``; that hatch is
for real document paths the model deliberately omits.
"""

from __future__ import annotations

import pytest

import main  # noqa: F401  — imports every api.v1 route module so specs exist
from core.list_params import FilterDef, ListSpec
from core.list_spec_validation import (
    _check_spec,
    iter_list_specs,
    unvalidated_list_specs,
    validate_list_specs,
)
from schemas.audit_log_schema import AuditLogOut


@pytest.mark.unit
def test_no_list_spec_references_a_phantom_field():
    findings = validate_list_specs()
    assert findings == [], "ListSpec drift:\n" + "\n".join(
        f"  - {f}" for f in findings
    )


@pytest.mark.unit
def test_every_list_spec_declares_its_model():
    """A spec without ``model`` is invisible to the drift check.

    New specs must set it, otherwise the guard silently stops covering them.
    """
    assert unvalidated_list_specs() == []


@pytest.mark.unit
def test_specs_are_actually_discovered():
    """Guards the guard: if discovery breaks, the checks above pass vacuously."""
    specs = list(iter_list_specs())
    assert len(specs) >= 15, f"only discovered {len(specs)} ListSpecs"


# --- the detector itself ------------------------------------------------
#
# A green suite above proves nothing unless the checker can actually fail.
# These plant each phantom shape against a real schema and assert it's caught.


@pytest.mark.unit
class TestDetectorCatchesPlantedPhantoms:
    def _check(self, **kwargs):
        # AuditLogOut timestamps with `timestamp`, so override ListSpec's
        # `date_created` default_sort — otherwise every planted spec also
        # reports that (correct) finding and drowns out the one under test.
        kwargs.setdefault("default_sort", (("timestamp", -1),))
        return _check_spec("planted", ListSpec(model=AuditLogOut, **kwargs))

    def test_catches_phantom_sort_field(self):
        findings = self._check(sortable_fields=frozenset({"nope_not_a_field"}))
        assert [(f.kind, f.field) for f in findings] == [("sort", "nope_not_a_field")]

    def test_catches_phantom_search_field(self):
        findings = self._check(search_fields=("action", "details_summary"))
        assert [(f.kind, f.field) for f in findings] == [
            ("search", "details_summary")
        ]

    def test_catches_phantom_plain_filter(self):
        findings = self._check(
            filters={"operation": FilterDef(name="operation")},
        )
        assert [(f.kind, f.field) for f in findings] == [("filter", "operation")]

    def test_catches_phantom_range_target(self):
        findings = self._check(range_filters={"createdAt": "date_created"})
        assert [(f.kind, f.field) for f in findings] == [("range", "date_created")]

    def test_builder_filters_are_exempt(self):
        """A builder composes its own fragment — its name is not a path."""
        findings = self._check(
            filters={
                "operation": FilterDef(name="operation", builder=lambda vs: {})
            },
        )
        assert findings == []

    def test_external_filters_are_exempt(self):
        """Route-resolved joins (planTier, supportTier) query another
        collection and are popped before run_list."""
        findings = self._check(
            filters={"supportTier": FilterDef(name="supportTier", external=True)},
        )
        assert findings == []

    def test_extra_fields_escape_hatch_is_honoured(self):
        findings = self._check(
            sortable_fields=frozenset({"denormalised_thing"}),
            extra_fields=frozenset({"denormalised_thing"}),
        )
        assert findings == []

    def test_id_and_alias_are_always_valid(self):
        findings = self._check(sortable_fields=frozenset({"_id", "id"}))
        assert findings == []

    def test_dotted_paths_check_only_the_root(self):
        assert self._check(sortable_fields=frozenset({"details.reason"})) == []
        findings = self._check(sortable_fields=frozenset({"nope.reason"}))
        assert [(f.kind, f.field) for f in findings] == [("sort", "nope.reason")]

    def test_spec_without_model_is_skipped(self):
        findings = _check_spec("planted", ListSpec(sortable_fields=frozenset({"x"})))
        assert findings == []

    def test_catches_default_sort_on_a_missing_field(self):
        """ListSpec's default_sort defaults to `date_created`; a schema that
        timestamps differently must not silently inherit it."""
        findings = _check_spec("planted", ListSpec(model=AuditLogOut))
        assert [(f.kind, f.field) for f in findings] == [
            ("default_sort", "date_created")
        ]
