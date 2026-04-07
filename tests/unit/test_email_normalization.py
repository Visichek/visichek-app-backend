"""Tests for email normalization utility."""
from __future__ import annotations

import pytest
from core.email_utils import normalize_email


class TestNormalizeEmail:
    """Unit tests for normalize_email()."""

    # --- Gmail-specific normalization ---

    def test_gmail_strips_dots(self):
        assert normalize_email("john.doe@gmail.com") == "johndoe@gmail.com"

    def test_gmail_strips_plus_alias(self):
        assert normalize_email("john+work@gmail.com") == "john@gmail.com"

    def test_gmail_strips_dots_and_plus(self):
        assert normalize_email("j.o.h.n+alias@gmail.com") == "john@gmail.com"

    def test_googlemail_normalized_to_gmail(self):
        assert normalize_email("john.doe@googlemail.com") == "johndoe@gmail.com"

    def test_googlemail_plus_alias(self):
        assert normalize_email("user+test@googlemail.com") == "user@gmail.com"

    def test_gmail_case_insensitive_domain(self):
        assert normalize_email("User@Gmail.Com") == "user@gmail.com"

    def test_gmail_case_insensitive_local(self):
        assert normalize_email("John.Doe@GMAIL.COM") == "johndoe@gmail.com"

    # --- Non-Gmail normalization ---

    def test_non_gmail_strips_plus_alias(self):
        assert normalize_email("user+tag@company.com") == "user@company.com"

    def test_non_gmail_preserves_dots(self):
        assert normalize_email("first.last@company.com") == "first.last@company.com"

    def test_non_gmail_case_insensitive(self):
        assert normalize_email("User@Company.Com") == "user@company.com"

    def test_non_gmail_strips_plus_preserves_dots(self):
        assert normalize_email("first.last+alias@outlook.com") == "first.last@outlook.com"

    # --- Edge cases ---

    def test_no_plus_no_dots(self):
        assert normalize_email("plain@example.com") == "plain@example.com"

    def test_email_with_whitespace_stripped(self):
        assert normalize_email("  user@gmail.com  ") == "user@gmail.com"

    def test_multiple_plus_signs_only_first_matters(self):
        assert normalize_email("user+a+b@gmail.com") == "user@gmail.com"

    # --- Equivalence checks ---

    def test_gmail_equivalence(self):
        """Several Gmail addresses that should all normalize to the same value."""
        variants = [
            "uririnathaniel@gmail.com",
            "uririnathaniel+1@gmail.com",
            "uriri.nathaniel@gmail.com",
            "u.r.i.r.i.n.a.t.h.a.n.i.e.l@gmail.com",
            "uririnathaniel+work@googlemail.com",
        ]
        normalized = {normalize_email(e) for e in variants}
        assert len(normalized) == 1
        assert normalized.pop() == "uririnathaniel@gmail.com"

    def test_non_gmail_equivalence(self):
        """Plus aliases on non-Gmail should normalize identically."""
        a = normalize_email("dev@company.com")
        b = normalize_email("dev+noreply@company.com")
        assert a == b

    def test_non_gmail_dots_not_equivalent(self):
        """On non-Gmail, dots matter."""
        a = normalize_email("first.last@company.com")
        b = normalize_email("firstlast@company.com")
        assert a != b
