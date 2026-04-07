"""
Email Normalization Utilities
=============================

Normalizes email addresses to prevent duplicate accounts using alias tricks.

Gmail-specific:
  - Strips ``+alias`` suffixes (user+tag@gmail.com → user@gmail.com)
  - Removes dots from local part (u.s.e.r@gmail.com → user@gmail.com)
  - Normalizes @googlemail.com → @gmail.com

Other providers:
  - Only strips ``+alias`` suffixes (dots may be significant)
  - Lowercases domain (User@Example.COM → user@example.com)
"""

from __future__ import annotations

_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


def normalize_email(email: str) -> str:
    """Return a canonical form of *email* for uniqueness checks.

    >>> normalize_email("John.Doe+work@Gmail.COM")
    'johndoe@gmail.com'
    >>> normalize_email("user+tag@outlook.com")
    'user@outlook.com'
    >>> normalize_email("some.user@company.co")
    'some.user@company.co'
    """
    if "@" not in email:
        return email.lower().strip()

    local, domain = email.rsplit("@", 1)
    domain = domain.lower().strip()
    local = local.lower().strip()

    # Strip +alias for ALL providers
    if "+" in local:
        local = local.split("+", 1)[0]

    # Gmail-specific: remove dots and normalize domain
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"

    return f"{local}@{domain}"
