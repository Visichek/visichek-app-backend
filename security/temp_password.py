"""Expiry policy for admin-issued temporary passwords.

An account created or reset by an authority gets a random temp password, mailed
in cleartext, with ``must_change_password=True``. Until now that password was
valid forever: the email sat in an inbox indefinitely, and anyone who later read
it — mailbox compromise, a forwarded thread, a shared inbox, an ex-employee's
archive — could still sign in, because nothing ever aged the credential out.

A temporary password that never expires is just a password with a worse
distribution channel. This module bounds its life.

Semantics:

* The clock starts when the temp password is issued (``must_change_password_at``).
* Once past the TTL, the temp password no longer authenticates. The account is
  NOT locked or deleted — it simply needs a fresh reset, which any authority
  path can issue. The recovery route is ordinary, not an incident.
* **Legacy rows are grandfathered.** A row carrying ``must_change_password=True``
  with no ``must_change_password_at`` pre-dates this policy; we cannot know when
  its password was issued, and guessing would lock real users out of accounts
  they have not yet had a chance to activate. Those rows stay valid until they
  are reset, at which point they gain a stamp and start expiring normally.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from fastapi import HTTPException

DEFAULT_TEMP_PASSWORD_TTL_HOURS = 72


def temp_password_ttl_seconds() -> int:
    """TTL for an issued temporary password, from ``TEMP_PASSWORD_TTL_HOURS``."""
    try:
        hours = int(
            os.getenv("TEMP_PASSWORD_TTL_HOURS", str(DEFAULT_TEMP_PASSWORD_TTL_HOURS))
        )
    except (TypeError, ValueError):
        hours = DEFAULT_TEMP_PASSWORD_TTL_HOURS
    if hours <= 0:
        hours = DEFAULT_TEMP_PASSWORD_TTL_HOURS
    return hours * 3600


def issued_at_now() -> int:
    """Stamp to write alongside ``must_change_password=True`` at every mint site."""
    return int(time.time())


def is_temp_password_expired(row: Mapping[str, Any] | Any) -> bool:
    """True when this row's temporary password has aged out.

    Accepts a raw Mongo dict or a Pydantic model. Returns False for accounts
    that aren't on a temp password at all, and False for legacy stamp-less rows
    (see module docstring).
    """
    if isinstance(row, Mapping):
        must_change = row.get("must_change_password")
        issued_at = row.get("must_change_password_at")
    else:
        must_change = getattr(row, "must_change_password", None)
        issued_at = getattr(row, "must_change_password_at", None)

    if not must_change:
        return False
    if not issued_at:
        return False  # legacy row — grandfathered, no issue time to age from

    return (int(time.time()) - int(issued_at)) > temp_password_ttl_seconds()


def raise_temp_password_expired() -> None:
    """Refuse a login/change made with an aged-out temporary password."""
    hours = temp_password_ttl_seconds() // 3600
    raise HTTPException(
        status_code=401,
        detail=(
            f"This temporary password has expired (valid for {hours} hours). "
            "Ask an administrator to send you a new one."
        ),
    )
