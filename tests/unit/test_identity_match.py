"""Tests for submitted-vs-extracted identity reconciliation.

Two failure modes matter equally and pull in opposite directions:

* **False accept** — an impostor using a valid ID that isn't theirs walks in
  marked "Verified". This is the hole the module exists to close.
* **False reject** — a legitimate visitor is flagged because the registry spells
  their name differently than they typed it. Nigerian registry data routinely
  reorders names, drops/adds middle names, and varies transliteration.

A matcher that only defends against one of these is not usable, so both are
pinned here.
"""

from __future__ import annotations

import pytest

from services.identity_match import compare_names, match_identity


class TestLegitimateVisitorsAreNotRejected:
    @pytest.mark.parametrize(
        "typed,extracted",
        [
            ("nathaniel uriri", "NATHANIEL URIRI"),  # casing
            ("Ada Okafor", "OKAFOR ADA"),  # registry reorders names
            ("Ada Okafor", "OKAFOR ADAEZE NGOZI"),  # registry has middle names
            ("Ada N. Okafor", "Ada Ngozi Okafor"),  # initial vs full middle name
            ("Dr. Ada Okafor", "ADA OKAFOR"),  # honorific
            ("Chinedu Muhammed", "Chinedu Mohammad"),  # transliteration drift
            ("Zainab O'Brien-Bello", "ZAINAB OBRIEN BELLO"),  # punctuation
        ],
    )
    def test_same_person_passes(self, typed, extracted):
        result = match_identity(submitted_name=typed, extracted_name=extracted)
        assert result.passed, f"{typed!r} vs {extracted!r} → {result.reasons}"


class TestImpostorsAreCaught:
    @pytest.mark.parametrize(
        "typed,extracted",
        [
            ("John Doe", "Adebayo Ogunlesi"),  # wholly different person
            ("Ada Okafor", "Ada Adeyemi"),  # same first name, different surname
            ("Chinedu Okafor", "Ngozi Okafor"),  # same surname, different person
        ],
    )
    def test_different_person_fails(self, typed, extracted):
        result = match_identity(submitted_name=typed, extracted_name=extracted)
        assert not result.passed
        assert "name mismatch" in result.reason_text

    def test_missing_extracted_name_is_not_a_match(self):
        """A 'success' with no identity to compare confirms nothing."""
        result = match_identity(submitted_name="John Doe", extracted_name=None)
        assert not result.passed
        assert "no name" in result.reason_text

    def test_dob_mismatch_fails_even_when_name_matches(self):
        result = match_identity(
            submitted_name="Ada Okafor",
            extracted_name="Ada Okafor",
            submitted_dob="1990-04-07",
            extracted_dob="1975-11-23",
        )
        assert not result.passed
        assert "date-of-birth mismatch" in result.reason_text

    def test_id_number_mismatch_fails(self):
        result = match_identity(
            submitted_name="Ada Okafor",
            extracted_name="Ada Okafor",
            submitted_id_number="12345678901",
            extracted_id_number="99999999999",
        )
        assert not result.passed
        assert "ID number mismatch" in result.reason_text


class TestDobFormatsReconcile:
    def test_differently_formatted_same_dob_passes(self):
        result = match_identity(
            submitted_name="Ada Okafor",
            extracted_name="Ada Okafor",
            submitted_dob="07/04/1990",
            extracted_dob="1990-04-07",
        )
        assert result.passed

    def test_absent_dob_is_unknown_not_mismatch(self):
        result = match_identity(
            submitted_name="Ada Okafor",
            extracted_name="Ada Okafor",
            submitted_dob=None,
            extracted_dob="1990-04-07",
        )
        assert result.passed
        assert result.dob_match is None


def test_name_score_is_reported():
    assert compare_names("Ada Okafor", "Ada Okafor") == 1.0
    assert compare_names("John Doe", "Adebayo Ogunlesi") == 0.0
