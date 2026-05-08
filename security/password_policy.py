"""
Password security policy module.

Provides:
- Popular/common password checking (blocks top commonly-breached passwords)
- Password strength validation (length, complexity requirements)
- Account lockout after failed login attempts
- Password history to prevent reuse

The numeric thresholds (length, lockout count, lockout duration, history
depth) are pulled from ``core.security_policy`` which mirrors the
platform-settings singleton. Module-level constants below are kept as
the *fallback floor* used by Pydantic schema validators when the cache
has not been primed yet.

Usage in schemas (sync — uses cached or default policy):
    from security.password_policy import validate_password_strength

Usage in services (async — passes a freshly-loaded policy):
    from core.security_policy import get_security_policy
    from security.password_policy import (
        check_login_lockout,
        record_failed_login,
        clear_failed_logins,
        check_password_history,
        record_password_in_history,
    )
"""

from __future__ import annotations

import re
import time
from typing import List, Optional

from pydantic import BaseModel

from core.security_policy import SecurityPolicy, get_security_policy_sync

# ---------------------------------------------------------------------------
# Default / floor constants — kept in sync with SecurityPolicy() defaults
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
SPECIAL_CHARS = r"""!@#$%^&*()_+-=[]{}|;':",./<>?`~"""

# Account lockout defaults (overridden by platform settings)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes

# Password history default (overridden by platform settings)
PASSWORD_HISTORY_COUNT = 5  # Prevent reusing last N passwords

# ---------------------------------------------------------------------------
# Common / breached passwords set
# ---------------------------------------------------------------------------

# Top commonly-breached passwords. In production, load from a file or use
# the haveibeenpwned k-anonymity API.  This embedded set covers the most
# egregious offenders to block without needing external dependencies.
_COMMON_PASSWORDS: set[str] = {
    "123456",
    "password",
    "12345678",
    "qwerty",
    "123456789",
    "12345",
    "1234",
    "111111",
    "1234567",
    "dragon",
    "123123",
    "baseball",
    "abc123",
    "football",
    "monkey",
    "letmein",
    "shadow",
    "master",
    "666666",
    "qwertyuiop",
    "123321",
    "mustang",
    "1234567890",
    "michael",
    "654321",
    "superman",
    "1qaz2wsx",
    "7777777",
    "121212",
    "000000",
    "qazwsx",
    "123qwe",
    "killer",
    "trustno1",
    "jordan",
    "jennifer",
    "zxcvbnm",
    "asdfgh",
    "hunter",
    "buster",
    "soccer",
    "harley",
    "batman",
    "andrew",
    "tigger",
    "sunshine",
    "iloveyou",
    "2000",
    "charlie",
    "robert",
    "thomas",
    "hockey",
    "ranger",
    "daniel",
    "starwars",
    "klaster",
    "112233",
    "george",
    "computer",
    "michelle",
    "jessica",
    "pepper",
    "1111",
    "zxcvbn",
    "555555",
    "11111111",
    "131313",
    "freedom",
    "777777",
    "pass",
    "maggie",
    "159753",
    "aaaaaa",
    "ginger",
    "princess",
    "joshua",
    "cheese",
    "amanda",
    "summer",
    "love",
    "ashley",
    "nicole",
    "chelsea",
    "biteme",
    "matthew",
    "access",
    "yankees",
    "987654321",
    "dallas",
    "austin",
    "thunder",
    "taylor",
    "matrix",
    "minecraft",
    "william",
    "corvette",
    "hello",
    "martin",
    "heather",
    "secret",
    "merlin",
    "diamond",
    "1234qwer",
    "gfhjkm",
    "hammer",
    "silver",
    "222222",
    "88888888",
    "anthony",
    "justin",
    "test",
    "bailey",
    "q1w2e3r4t5",
    "patrick",
    "internet",
    "scooter",
    "orange",
    "golfer",
    "cookie",
    "richard",
    "samantha",
    "bigdog",
    "guitar",
    "jackson",
    "whatever",
    "mickey",
    "chicken",
    "sparky",
    "snoopy",
    "maverick",
    "phoenix",
    "camaro",
    "peanut",
    "morgan",
    "welcome",
    "falcon",
    "cowboy",
    "ferrari",
    "samsung",
    "andrea",
    "smokey",
    "steelers",
    "joseph",
    "mercedes",
    "dakota",
    "arsenal",
    "eagles",
    "melissa",
    "boomer",
    "booboo",
    "spider",
    "nascar",
    "monster",
    "tigers",
    "yellow",
    "xxxxxx",
    "123123123",
    "gateway",
    "marina",
    "diablo",
    "bulldog",
    "qwer1234",
    "compaq",
    "purple",
    "hardcore",
    "banana",
    "junior",
    "hannah",
    "123654",
    "porsche",
    "lakers",
    "iceman",
    "money",
    "cowboys",
    "987654",
    "london",
    "tennis",
    "999999",
    "ncc1701",
    "coffee",
    "scooby",
    "0000",
    "miller",
    "boston",
    "q1w2e3r4",
    "brandon",
    "yamaha",
    "chester",
    "mother",
    "forever",
    "johnny",
    "edward",
    "333333",
    "oliver",
    "redsox",
    "player",
    "nikita",
    "knight",
    "fender",
    "barney",
    "midnight",
    "please",
    "brandy",
    "badboy",
    "slayer",
    "rangers",
    "charles",
    "flower",
    "bigdaddy",
    "rabbit",
    "wizard",
    "jasper",
    "enter",
    "rachel",
    "chris",
    "steven",
    "winner",
    "adidas",
    "victoria",
    "natasha",
    "1q2w3e4r",
    "jasmine",
    "winter",
    "prince",
    "marine",
    "ghbdtn",
    "fishing",
    "cocacola",
    "casper",
    "oscar",
    "tucker",
    "patrick",
    "spirit",
    "passw0rd",
    "admin123",
    "letmein1",
    "welcome1",
    "password1",
    "password123",
    "admin",
    "root",
    "toor",
    "pass123",
    "test123",
    "guest",
    "master123",
    "changeme",
    "1q2w3e",
    "qwerty123",
    "admin1234",
    "p@ssw0rd",
}


def is_common_password(password: str) -> bool:
    """Check if the password is in the common/breached passwords list."""
    return password.lower().strip() in _COMMON_PASSWORDS


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------


class PasswordStrengthResult(BaseModel):
    """Result of password strength validation."""

    is_valid: bool
    errors: List[str]
    score: int  # 0-5 strength score


def validate_password_strength(
    password: str, policy: Optional[SecurityPolicy] = None
) -> PasswordStrengthResult:
    """Validate a password against the platform-configured policy.

    ``policy`` is the platform's current ``SecurityPolicy`` snapshot. When
    omitted, the cached snapshot from ``get_security_policy_sync()`` is
    used so synchronous Pydantic validators stay synchronous; the cache
    is primed at startup and refreshed whenever platform settings are
    updated.
    """
    p = policy or get_security_policy_sync()
    errors: List[str] = []
    score = 0

    # Length check
    if len(password) < p.password_min_length:
        errors.append(
            f"Password must be at least {p.password_min_length} characters long"
        )
    elif len(password) >= 12:
        score += 1  # Bonus for longer passwords

    if len(password) > p.password_max_length:
        errors.append(f"Password must not exceed {p.password_max_length} characters")

    # Uppercase check
    if p.password_require_uppercase and not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")
    else:
        score += 1

    # Lowercase check
    if p.password_require_lowercase and not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")
    else:
        score += 1

    # Digit check
    if p.password_require_number and not re.search(r"\d", password):
        errors.append("Password must contain at least one digit")
    else:
        score += 1

    # Special character check
    if p.password_require_special_char and not re.search(
        r"[!@#$%^&*()\-_+=\[\]{}|;':\",./<>?`~]", password
    ):
        errors.append("Password must contain at least one special character")
    else:
        score += 1

    # Common password check
    if is_common_password(password):
        errors.append(
            "This password is too common and has appeared in data breaches. "
            "Please choose a more unique password."
        )

    # Sequential/repeated character check
    if _has_sequential_chars(password, 4):
        errors.append(
            "Password must not contain 4 or more sequential characters (e.g. 1234, abcd)"
        )

    if _has_repeated_chars(password, 4):
        errors.append(
            "Password must not contain 4 or more repeated characters (e.g. aaaa, 1111)"
        )

    return PasswordStrengthResult(
        is_valid=len(errors) == 0,
        errors=errors,
        score=min(score, 5),
    )


def _has_sequential_chars(password: str, count: int) -> bool:
    """Check for sequential character runs (ascending or descending)."""
    pw = password.lower()
    for i in range(len(pw) - count + 1):
        chunk = pw[i : i + count]
        # Check ascending
        if all(ord(chunk[j + 1]) == ord(chunk[j]) + 1 for j in range(count - 1)):
            return True
        # Check descending
        if all(ord(chunk[j + 1]) == ord(chunk[j]) - 1 for j in range(count - 1)):
            return True
    return False


def _has_repeated_chars(password: str, count: int) -> bool:
    """Check for repeated character runs."""
    for i in range(len(password) - count + 1):
        if len(set(password[i : i + count])) == 1:
            return True
    return False


# ---------------------------------------------------------------------------
# Account lockout (uses MongoDB collection: login_attempts)
# ---------------------------------------------------------------------------


async def _resolve_policy(policy: Optional[SecurityPolicy]) -> SecurityPolicy:
    if policy is not None:
        return policy
    from core.security_policy import get_security_policy

    return await get_security_policy()


async def check_login_lockout(identifier: str) -> Optional[dict]:
    """Check if an account is currently locked out due to failed login attempts.

    The lockout state is stored on the ``login_attempts`` record itself, so
    no policy lookup is needed here — the policy only matters when *recording*
    a failure (where the threshold and duration are applied).
    """
    from core.database import db

    record = await db["login_attempts"].find_one({"identifier": identifier})
    if not record:
        return None

    now = int(time.time())

    if record.get("locked_until") and record["locked_until"] > now:
        remaining = record["locked_until"] - now
        return {
            "locked": True,
            "failed_attempts": record.get("failed_count", 0),
            "locked_until": record["locked_until"],
            "remaining_seconds": remaining,
        }

    # If lockout has expired, reset the counter
    if record.get("locked_until") and record["locked_until"] <= now:
        await db["login_attempts"].update_one(
            {"identifier": identifier},
            {"$set": {"failed_count": 0, "locked_until": None}},
        )

    return None


async def record_failed_login(
    identifier: str, policy: Optional[SecurityPolicy] = None
) -> dict:
    """Record a failed login attempt. Returns lockout status after recording."""
    from core.database import db

    p = await _resolve_policy(policy)
    max_attempts = p.max_failed_login_attempts
    lockout_seconds = p.lockout_duration_minutes * 60

    now = int(time.time())

    result = await db["login_attempts"].find_one_and_update(
        {"identifier": identifier},
        {
            "$inc": {"failed_count": 1},
            "$set": {"last_failed_at": now},
            "$setOnInsert": {"identifier": identifier, "created_at": now},
        },
        upsert=True,
        return_document=True,
    )

    failed_count = result.get("failed_count", 1) if result else 1

    # Check if we should lock the account
    if failed_count >= max_attempts:
        locked_until = now + lockout_seconds
        await db["login_attempts"].update_one(
            {"identifier": identifier},
            {"$set": {"locked_until": locked_until}},
        )
        return {
            "locked": True,
            "failed_count": failed_count,
            "locked_until": locked_until,
            "remaining_seconds": lockout_seconds,
        }

    return {
        "locked": False,
        "failed_count": failed_count,
        "attempts_remaining": max_attempts - failed_count,
    }


async def clear_failed_logins(identifier: str) -> None:
    """Clear failed login attempts after a successful login."""
    from core.database import db

    await db["login_attempts"].delete_one({"identifier": identifier})


# ---------------------------------------------------------------------------
# Password history (uses MongoDB collection: password_history)
# ---------------------------------------------------------------------------


async def check_password_history(
    user_id: str,
    new_password: str,
    role: str = "admin",
    history_count: Optional[int] = None,
) -> bool:
    """Check if the new password was recently used.

    Returns True if the password is safe (not in history), False if reused.
    ``history_count`` defaults to the platform-configured value when omitted.
    """
    from core.database import db
    from security.hash import check_password

    if history_count is None:
        policy = await _resolve_policy(None)
        history_count = policy.password_history_count

    if history_count <= 0:
        return True

    cursor = (
        db["password_history"]
        .find(
            {"user_id": user_id, "role": role},
        )
        .sort("changed_at", -1)
        .limit(history_count)
    )

    records = await cursor.to_list(length=history_count)

    for record in records:
        old_hash = record.get("password_hash", "")
        if check_password(new_password, old_hash):
            return False  # Password was recently used

    return True  # Safe to use


async def record_password_in_history(
    user_id: str,
    password_hash: str | bytes,
    role: str = "admin",
    history_count: Optional[int] = None,
) -> None:
    """Record a password hash in the user's history.

    Keeps only the last ``history_count`` entries (default: platform policy).
    """
    from core.database import db

    if history_count is None:
        policy = await _resolve_policy(None)
        history_count = policy.password_history_count

    now = int(time.time())

    # Ensure the hash is stored as a string
    if isinstance(password_hash, bytes):
        password_hash = password_hash.decode("utf-8")

    await db["password_history"].insert_one(
        {
            "user_id": user_id,
            "role": role,
            "password_hash": password_hash,
            "changed_at": now,
        }
    )

    # Prune old entries beyond the history limit
    if history_count <= 0:
        return

    count = await db["password_history"].count_documents(
        {"user_id": user_id, "role": role}
    )
    if count > history_count:
        oldest = (
            db["password_history"]
            .find(
                {"user_id": user_id, "role": role},
            )
            .sort("changed_at", 1)
            .limit(count - history_count)
        )

        old_ids = [doc["_id"] async for doc in oldest]
        if old_ids:
            await db["password_history"].delete_many({"_id": {"$in": old_ids}})
