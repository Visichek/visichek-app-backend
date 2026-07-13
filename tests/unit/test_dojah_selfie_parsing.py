"""Dojah payload -> face-match signal.

The whole face check is worthless if a ``False`` verdict is dropped on the way
in. A truthiness-based scan (``_first_truthy``) silently skips ``False`` and
reports "no verdict", which downgrades "these are different people" into "we
don't know" — and "we don't know" does not block a check-in. Pin the parse.
"""

from __future__ import annotations

from core.kyc.dojah_provider import _entity_to_details, _first_bool


class TestFirstBoolPreservesFalse:
    def test_false_survives(self):
        assert _first_bool(False) is False

    def test_true_survives(self):
        assert _first_bool(True) is True

    def test_absent_is_none(self):
        assert _first_bool(None, None) is None

    def test_first_real_bool_wins_over_later_values(self):
        assert _first_bool(False, True) is False

    def test_string_verdicts_are_understood(self):
        assert _first_bool("no_match") is False
        assert _first_bool("match") is True
        assert _first_bool("maybe") is None


class TestEntityToDetails:
    def test_failed_selfie_is_carried_through(self):
        details = _entity_to_details(
            reference_id="ref-1",
            entity={
                "verification_status": "Completed",
                "selfie": {"match": False, "confidence_value": 12.5},
                "government_data": {"full_name": "Adebayo Ogunlesi"},
            },
        )
        assert details.status == "success"
        # Dojah says the check "completed" — but the face did not match.
        assert details.selfie_match is False
        assert details.confidence == 12.5

    def test_matching_selfie_is_carried_through(self):
        details = _entity_to_details(
            reference_id="ref-2",
            entity={
                "verification_status": "Completed",
                "selfie": {"match": True, "confidence_value": 98.0},
                "government_data": {"full_name": "Ada Okafor"},
            },
        )
        assert details.selfie_match is True
        assert details.confidence == 98.0

    def test_missing_selfie_block_is_none_not_false(self):
        details = _entity_to_details(
            reference_id="ref-3",
            entity={
                "verification_status": "Completed",
                "government_data": {"full_name": "Ada Okafor"},
            },
        )
        assert details.selfie_match is None
