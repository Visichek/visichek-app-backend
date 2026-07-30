"""``?operation=`` on the audit log is derived from `action`, not a field.

Audit rows carry no `operation` key — the CRUD verb is the tail of
`action` (`<resource>.<verb>`, past tense). The old FilterDef queried a bare
`operation` path, so the Operation control on the tenant audit page returned
zero rows for every selection.
"""

from __future__ import annotations

import re

import pytest

from api.v1.audit_route import _audit_operation_builder


def _matches(fragment: dict, action: str) -> bool:
    """Apply the built fragment to an action string the way Mongo would."""
    spec = fragment["action"]
    if "$in" in spec:
        return action in spec["$in"]
    return re.search(spec["$regex"], action) is not None


@pytest.mark.unit
class TestAuditOperationBuilder:
    def test_create_matches_past_tense_actions(self):
        frag = _audit_operation_builder(["create"])
        assert _matches(frag, "appointment.created")
        assert _matches(frag, "addon.created")
        assert not _matches(frag, "appointment.updated")
        assert not _matches(frag, "appointment.deleted")

    def test_update_and_delete_are_disjoint(self):
        upd = _audit_operation_builder(["update"])
        dele = _audit_operation_builder(["delete"])
        assert _matches(upd, "subscription.updated")
        assert not _matches(upd, "subscription.deleted")
        assert _matches(dele, "subscription.deleted")
        assert not _matches(dele, "subscription.updated")

    def test_bare_verb_form_also_matches(self):
        """The frontend's operationVariant recognises both `.create` and
        `.created`; the filter must not miss the bare form."""
        frag = _audit_operation_builder(["create"])
        assert _matches(frag, "incident.create")

    def test_suffix_is_anchored_so_similar_verbs_dont_bleed(self):
        frag = _audit_operation_builder(["create"])
        # "purchase_initiated" ends the action but is not a create verb.
        assert not _matches(frag, "addon.purchase_initiated")
        # A verb must be the final path segment, not a substring.
        assert not _matches(frag, "plan.created_from_template")

    def test_multiple_operations_union(self):
        frag = _audit_operation_builder(["create", "delete"])
        assert _matches(frag, "branch.created")
        assert _matches(frag, "branch.deleted")
        assert not _matches(frag, "branch.updated")

    def test_unknown_operation_matches_nothing_not_everything(self):
        """A filter that can't be interpreted must never widen the result set."""
        frag = _audit_operation_builder(["bogus"])
        assert frag == {"action": {"$in": []}}
        assert not _matches(frag, "branch.created")

    def test_read_matches_nothing_today_by_design(self):
        """No read events are audited, so `read` legitimately returns none —
        but it must not throw or match write actions."""
        frag = _audit_operation_builder(["read"])
        assert not _matches(frag, "branch.created")
        assert not _matches(frag, "branch.updated")
