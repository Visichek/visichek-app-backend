from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import List

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from security.hash import hash_password

logger = logging.getLogger(__name__)

TOTP_COLLECTION = "totp_secrets"
BACKUP_CODES_COLLECTION = "backup_codes"


def _generate_totp_secret() -> str:
    """Generate a base32-encoded TOTP secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8")


def _generate_backup_codes(count: int = 8) -> List[str]:
    """Generate one-time-use backup codes."""
    return [secrets.token_hex(4).upper() for _ in range(count)]


def _build_otpauth_uri(secret: str, email: str, issuer: str = "VisiChek") -> str:
    """Build an otpauth:// URI for QR code rendering."""
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


async def setup_two_factor(
    user_id: str,
    user_type: str,
    email: str,
) -> dict:
    """Initiate 2FA setup. Returns secret, QR URI, and backup codes.

    The 2FA is not active until verified with verify_two_factor_setup().
    """
    # Check if already set up
    existing = await db[TOTP_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "verified": True}
    )
    if existing:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")

    secret = _generate_totp_secret()
    backup_codes = _generate_backup_codes()

    # Store pending setup (not yet verified)
    await db[TOTP_COLLECTION].delete_many(
        {"user_id": user_id, "user_type": user_type, "verified": False}
    )
    await db[TOTP_COLLECTION].insert_one({
        "user_id": user_id,
        "user_type": user_type,
        "secret": secret,
        "verified": False,
    })

    # Store hashed backup codes
    await db[BACKUP_CODES_COLLECTION].delete_many(
        {"user_id": user_id, "user_type": user_type}
    )
    for code in backup_codes:
        await db[BACKUP_CODES_COLLECTION].insert_one({
            "user_id": user_id,
            "user_type": user_type,
            "code_hash": hashlib.sha256(code.encode()).hexdigest(),
            "used": False,
        })

    qr_uri = _build_otpauth_uri(secret, email)

    return {
        "secret": secret,
        "qr_code_uri": qr_uri,
        "backup_codes": backup_codes,
    }


async def verify_two_factor_setup(
    user_id: str,
    user_type: str,
    code: str,
) -> List[str]:
    """Verify TOTP code to confirm 2FA setup. Returns backup codes."""
    pending = await db[TOTP_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "verified": False}
    )
    if not pending:
        raise HTTPException(status_code=404, detail="No pending 2FA setup found")

    # Verify the TOTP code
    if not _verify_totp_code(pending["secret"], code):
        # Also check backup codes
        if not await _consume_backup_code(user_id, user_type, code):
            raise HTTPException(status_code=401, detail="Invalid verification code")

    # Mark as verified
    await db[TOTP_COLLECTION].update_one(
        {"_id": pending["_id"]},
        {"$set": {"verified": True}},
    )

    # Update user record to reflect 2FA enabled
    collection = "admins" if user_type == "admin" else "system_users"
    await db[collection].update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"mfa_enabled": True, "mfa_method": "totp"}},
    )

    # Return the backup codes that were stored during setup
    cursor = db[BACKUP_CODES_COLLECTION].find(
        {"user_id": user_id, "user_type": user_type, "used": False},
        {"code_hash": 0},
    )
    # We cannot recover the plaintext codes from hashes, so re-generate
    # and replace with fresh ones that the user can actually save
    new_codes = _generate_backup_codes()
    await db[BACKUP_CODES_COLLECTION].delete_many(
        {"user_id": user_id, "user_type": user_type}
    )
    for c in new_codes:
        await db[BACKUP_CODES_COLLECTION].insert_one({
            "user_id": user_id,
            "user_type": user_type,
            "code_hash": hashlib.sha256(c.encode()).hexdigest(),
            "used": False,
        })

    return new_codes


async def disable_two_factor(
    user_id: str,
    user_type: str,
    code: str,
) -> None:
    """Disable 2FA after verifying the current code."""
    totp_record = await db[TOTP_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "verified": True}
    )
    if not totp_record:
        raise HTTPException(status_code=404, detail="Two-factor authentication is not enabled")

    if not _verify_totp_code(totp_record["secret"], code):
        if not await _consume_backup_code(user_id, user_type, code):
            raise HTTPException(status_code=401, detail="Invalid verification code")

    # Remove TOTP secret and backup codes
    await db[TOTP_COLLECTION].delete_many({"user_id": user_id, "user_type": user_type})
    await db[BACKUP_CODES_COLLECTION].delete_many({"user_id": user_id, "user_type": user_type})

    # Update user record
    collection = "admins" if user_type == "admin" else "system_users"
    await db[collection].update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"mfa_enabled": False, "mfa_method": None}},
    )


async def disable_two_factor_with_password(
    user_id: str,
    user_type: str,
    password: str,
) -> None:
    """Disable 2FA after verifying the user's password."""
    from security.hash import check_password as _check_pw

    collection = "admins" if user_type == "admin" else "system_users"
    user_doc = await db[collection].find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")

    if not _check_pw(password, user_doc["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    totp_record = await db[TOTP_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "verified": True}
    )
    if not totp_record:
        raise HTTPException(status_code=404, detail="Two-factor authentication is not enabled")

    await db[TOTP_COLLECTION].delete_many({"user_id": user_id, "user_type": user_type})
    await db[BACKUP_CODES_COLLECTION].delete_many({"user_id": user_id, "user_type": user_type})

    await db[collection].update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"mfa_enabled": False, "mfa_method": None}},
    )


async def regenerate_backup_codes_with_password(
    user_id: str,
    user_type: str,
    password: str,
) -> List[str]:
    """Generate new backup codes after verifying the user's password."""
    from security.hash import check_password as _check_pw

    collection = "admins" if user_type == "admin" else "system_users"
    user_doc = await db[collection].find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")

    if not _check_pw(password, user_doc["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    return await regenerate_backup_codes(user_id, user_type)


async def regenerate_backup_codes(
    user_id: str,
    user_type: str,
) -> List[str]:
    """Generate new backup codes, invalidating old ones."""
    totp_record = await db[TOTP_COLLECTION].find_one(
        {"user_id": user_id, "user_type": user_type, "verified": True}
    )
    if not totp_record:
        raise HTTPException(status_code=404, detail="Two-factor authentication is not enabled")

    backup_codes = _generate_backup_codes()

    await db[BACKUP_CODES_COLLECTION].delete_many(
        {"user_id": user_id, "user_type": user_type}
    )
    for code in backup_codes:
        await db[BACKUP_CODES_COLLECTION].insert_one({
            "user_id": user_id,
            "user_type": user_type,
            "code_hash": hashlib.sha256(code.encode()).hexdigest(),
            "used": False,
        })

    return backup_codes


def _verify_totp_code(secret: str, code: str) -> bool:
    """Verify a TOTP code against the secret.

    Uses HMAC-based OTP with a 30-second window, allowing +-1 window drift.
    """
    import hmac
    import struct
    import time as _time

    try:
        key = base64.b32decode(secret, casefold=True)
    except Exception:
        return False

    # Check current window and +-1 for clock drift
    current_time = int(_time.time())
    for offset in (-1, 0, 1):
        counter = (current_time // 30) + offset
        msg = struct.pack(">Q", counter)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset_byte = h[-1] & 0x0F
        truncated = struct.unpack(">I", h[offset_byte:offset_byte + 4])[0]
        truncated &= 0x7FFFFFFF
        expected = str(truncated % 10**6).zfill(6)
        if hmac.compare_digest(expected, code):
            return True

    return False


async def _consume_backup_code(user_id: str, user_type: str, code: str) -> bool:
    """Try to use a backup code. Returns True if valid and consumed."""
    code_hash = hashlib.sha256(code.upper().encode()).hexdigest()
    result = await db[BACKUP_CODES_COLLECTION].update_one(
        {
            "user_id": user_id,
            "user_type": user_type,
            "code_hash": code_hash,
            "used": False,
        },
        {"$set": {"used": True}},
    )
    return result.modified_count > 0
