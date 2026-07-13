"""Compare the identity a visitor TYPED against the identity Dojah RETURNED.

Dojah answers one question: "is this a real, valid government identity?" It
does NOT answer "does this identity belong to the person filling in the form."
A visitor can enter their own name and someone else's NIN; Dojah happily
returns ``status: success`` with the *real owner's* details, and without this
module we would stamp ``verified=True`` on the impostor's check-in.

So the provider's success status is necessary but not sufficient. This module
supplies the missing half: it reconciles the submitted name / DOB / ID number
with the values the provider extracted, and reports whether they describe the
same person.

Design notes:

* **Names are compared on token sets, not strings.** Real people legitimately
  type "Ada N. Okafor" where the registry holds "OKAFOR ADAEZE NGOZI" —
  reordering, casing, punctuation, middle names, and honorifics are all normal
  and must not fail a genuine visitor. What must fail is a *different person*.
* **A missing extracted field is not a match.** If the provider returned no
  name at all we cannot claim the identity was confirmed, so we do not.
* The verdict is advisory data, not an exception — the caller decides what to
  do with a mismatch (we route it to a human rather than auto-rejecting, since
  a false reject strands a legitimate visitor at the door).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Honorifics and suffixes carry no identifying signal; a registry that omits
# "Dr" while the visitor types it must not read as a different person.
_NOISE_TOKENS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "prof",
        "engr",
        "esq",
        "chief",
        "alhaji",
        "alhaja",
        "hon",
        "rev",
        "pastor",
        "sir",
        "jr",
        "snr",
        "sr",
        "ii",
        "iii",
    }
)

# Below this, two surviving name tokens are not the same word.
#
# Calibrated against real Nigerian-registry drift rather than picked by feel.
# Measured SequenceMatcher ratios:
#
#   same name, spelling/transliteration variant   same name, different person
#   ------------------------------------------    ---------------------------
#   yusuf / yousouf      0.83                      okafor / okonkwo    0.46
#   okafor / okafur      0.83                      ada    / adeyemi    0.40
#   ngozi  / ngosi       0.80                      doe    / ogunlesi   0.36
#   ibrahim/ ibraheem    0.80                      john   / adebayo    0.18
#   muhammed/ mohammad   0.75                      okafor / adeyemi    0.15
#
# The two populations separate cleanly between 0.46 and 0.75, so 0.72 sits in
# the gap: it absorbs every variant above without admitting any distinct name
# below. Raising it to 0.85 (the intuitive choice) would reject "Muhammed" vs
# "Mohammad" — a real person, turned away at the door.
_TOKEN_SIMILARITY_THRESHOLD = 0.72

# Overall score required for a name to count as the same person.
_NAME_MATCH_THRESHOLD = 0.80


def _normalise(value: str) -> str:
    """Casefold, strip accents, and reduce to bare alphanumerics + spaces."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.casefold()
    return re.sub(r"[^a-z0-9\s]", " ", lowered)


def _tokens(value: str) -> list[str]:
    """Meaningful name tokens, honorifics and single letters removed.

    Single letters are dropped because they are initials — "Ada N. Okafor"
    and "Ada Ngozi Okafor" are the same person, and scoring the bare "n"
    against "ngozi" would only add noise.
    """
    return [
        t
        for t in _normalise(value).split()
        if len(t) > 1 and t not in _NOISE_TOKENS
    ]


def _token_matches(token: str, candidates: list[str]) -> bool:
    for other in candidates:
        if token == other:
            return True
        # An initial-vs-full-name pair ("ada" inside "adaeze") is a legitimate
        # registry/form difference, not a different person.
        if len(token) >= 3 and (token.startswith(other) or other.startswith(token)):
            return True
        if SequenceMatcher(None, token, other).ratio() >= _TOKEN_SIMILARITY_THRESHOLD:
            return True
    return False


def compare_names(submitted: str | None, extracted: str | None) -> float:
    """Score 0.0–1.0 that two name strings describe the same person.

    Order-insensitive: scores the fraction of the SHORTER name's tokens that
    are present in the longer one. This is deliberate — the registry commonly
    holds more names (middle names) than the visitor bothers to type, and that
    asymmetry must not be read as a mismatch. Requiring the *shorter* side to
    be fully contained still blocks a different person, whose tokens simply
    won't appear on the other side at all.
    """
    if not submitted or not extracted:
        return 0.0

    submitted_tokens = _tokens(submitted)
    extracted_tokens = _tokens(extracted)
    if not submitted_tokens or not extracted_tokens:
        return 0.0

    shorter, longer = (
        (submitted_tokens, extracted_tokens)
        if len(submitted_tokens) <= len(extracted_tokens)
        else (extracted_tokens, submitted_tokens)
    )
    matched = sum(1 for token in shorter if _token_matches(token, longer))
    return matched / len(shorter)


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def compare_dates_of_birth(submitted: str | None, extracted: str | None) -> bool | None:
    """True/False if both DOBs are present and comparable, else None (unknown).

    Compares on the digit multiset so ``1990-04-07``, ``07/04/1990`` and
    ``07-04-1990`` reconcile without us having to guess day/month ordering —
    guessing wrong would fail honest visitors on an ambiguous format.
    """
    if not submitted or not extracted:
        return None
    left, right = _digits(submitted), _digits(extracted)
    if not left or not right:
        return None
    if left == right:
        return True
    return sorted(left) == sorted(right)


def compare_id_numbers(submitted: str | None, extracted: str | None) -> bool | None:
    """True/False if both ID numbers are present, else None (unknown)."""
    if not submitted or not extracted:
        return None
    left, right = _digits(submitted), _digits(extracted)
    if not left or not right:
        return None
    return left == right


@dataclass
class IdentityMatchResult:
    """Verdict on whether the typed identity and the returned identity agree."""

    passed: bool
    name_score: float
    dob_match: bool | None = None
    id_number_match: bool | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str | None:
        return "; ".join(self.reasons) if self.reasons else None


def match_identity(
    *,
    submitted_name: str | None,
    extracted_name: str | None,
    submitted_dob: str | None = None,
    extracted_dob: str | None = None,
    submitted_id_number: str | None = None,
    extracted_id_number: str | None = None,
) -> IdentityMatchResult:
    """Reconcile a submitted identity against a provider-extracted one.

    Fails closed on absent evidence: if the provider gave us no name to compare,
    the identity is NOT confirmed, because we have nothing confirming it. A
    provider "success" with no extractable identity tells us a document was
    valid, not that it was *this visitor's* document.
    """
    reasons: list[str] = []

    name_score = compare_names(submitted_name, extracted_name)
    if not extracted_name:
        reasons.append("provider returned no name to verify against")
    elif name_score < _NAME_MATCH_THRESHOLD:
        reasons.append(
            f"name mismatch: visitor entered {submitted_name!r} but the ID "
            f"belongs to {extracted_name!r}"
        )

    dob_match = compare_dates_of_birth(submitted_dob, extracted_dob)
    if dob_match is False:
        reasons.append(
            f"date-of-birth mismatch: entered {submitted_dob!r}, ID says {extracted_dob!r}"
        )

    id_number_match = compare_id_numbers(submitted_id_number, extracted_id_number)
    if id_number_match is False:
        reasons.append(
            "ID number mismatch: the number entered is not the number on the "
            "verified document"
        )

    return IdentityMatchResult(
        passed=not reasons,
        name_score=round(name_score, 3),
        dob_match=dob_match,
        id_number_match=id_number_match,
        reasons=reasons,
    )
