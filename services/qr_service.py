from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from core.settings import get_settings


def sign_badge_token(
    session_id: str,
    expiry_hours: int = 24,
    *,
    expires_at: int | None = None,
) -> str:
    """Create an HMAC-signed QR token for a visit session badge.

    ``expires_at`` (epoch seconds), when given, wins over ``expiry_hours`` so
    the token's embedded expiry can match an exact badge-expiry timestamp
    (e.g. tenant end-of-day) instead of a whole-hour offset.
    """
    settings = get_settings()
    secret = settings.qr_signing_secret.encode("utf-8")
    expiry = (
        int(expires_at)
        if expires_at is not None
        else int(time.time()) + (expiry_hours * 3600)
    )
    payload = f"{session_id}|{expiry}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode(
        "utf-8"
    )
    return token


def verify_badge_token(token: str) -> str | None:
    """Verify an HMAC-signed QR token. Returns session_id if valid, None otherwise."""
    try:
        settings = get_settings()
        secret = settings.qr_signing_secret.encode("utf-8")
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) != 3:
            return None

        session_id, expiry_str, signature = parts
        expiry = int(expiry_str)

        # Check expiry
        if time.time() > expiry:
            return None

        # Verify HMAC
        payload = f"{session_id}|{expiry_str}"
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None

        return session_id
    except Exception:
        return None


def sign_registration_token(
    tenant_id: str,
    department_id: str | None = None,
    branch_id: str | None = None,
    expiry_hours: int = 720,
) -> str:
    """Create an HMAC-signed token for tenant registration QR codes. Default 30 day expiry."""
    settings = get_settings()
    secret = settings.qr_signing_secret.encode("utf-8")
    expiry = int(time.time()) + (expiry_hours * 3600)
    parts = [tenant_id, department_id or "", branch_id or "", str(expiry)]
    payload = "|".join(parts)
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode(
        "utf-8"
    )
    return token


def sign_checkin_capability(
    tenant_id: str,
    checkin_id: str,
    ttl_seconds: int = 30 * 60,
) -> str:
    """Mint a short-lived capability token for public KYC follow-up actions.

    Bound to ``(tenant_id, checkin_id, action="kyc", expiry, nonce)`` and
    HMAC-signed with the QR secret. Possession of a freshly-created
    check-in id is no longer enough to skip KYC or poll status — the caller
    must present this token, which is handed back only in the check-in
    creation response. The nonce makes each token unique so two check-ins
    never collide and a leaked token is traceable.
    """
    settings = get_settings()
    secret = settings.qr_signing_secret.encode("utf-8")
    expiry = int(time.time()) + ttl_seconds
    nonce = base64.urlsafe_b64encode(os.urandom(9)).decode("utf-8")
    payload = f"kyc|{tenant_id}|{checkin_id}|{expiry}|{nonce}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode(
        "utf-8"
    )


def verify_checkin_capability(token: str, *, checkin_id: str) -> bool:
    """Verify a KYC capability token is valid for ``checkin_id``.

    Returns True only when the token is well-formed, unexpired, signed with
    the current secret, scoped to ``action="kyc"``, and bound to the exact
    ``checkin_id`` the caller is acting on. Any mismatch (including a token
    minted for a different check-in) returns False.
    """
    try:
        settings = get_settings()
        secret = settings.qr_signing_secret.encode("utf-8")
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) != 6:
            return False
        action, tenant_id, tok_checkin_id, expiry_str, nonce, signature = parts
        if action != "kyc":
            return False
        if tok_checkin_id != checkin_id:
            return False
        if time.time() > int(expiry_str):
            return False
        payload = f"{action}|{tenant_id}|{tok_checkin_id}|{expiry_str}|{nonce}"
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


def verify_registration_token(token: str) -> dict | None:
    """Verify registration QR token. Returns {tenant_id, department_id, branch_id} or None."""
    try:
        settings = get_settings()
        secret = settings.qr_signing_secret.encode("utf-8")
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) != 5:
            return None
        tenant_id, department_id, branch_id, expiry_str, signature = parts
        if time.time() > int(expiry_str):
            return None
        payload = "|".join([tenant_id, department_id, branch_id, expiry_str])
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        return {
            "tenant_id": tenant_id,
            "department_id": department_id or None,
            "branch_id": branch_id or None,
        }
    except Exception:
        return None
