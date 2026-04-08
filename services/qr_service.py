from __future__ import annotations

import base64
import hashlib
import hmac
import time

from core.settings import get_settings


def sign_badge_token(session_id: str, expiry_hours: int = 24) -> str:
    """Create an HMAC-signed QR token for a visit session badge."""
    settings = get_settings()
    secret = settings.qr_signing_secret.encode("utf-8")
    expiry = int(time.time()) + (expiry_hours * 3600)
    payload = f"{session_id}|{expiry}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode("utf-8")
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


def sign_registration_token(tenant_id: str, department_id: str | None = None, branch_id: str | None = None, expiry_hours: int = 720) -> str:
    """Create an HMAC-signed token for tenant registration QR codes. Default 30 day expiry."""
    settings = get_settings()
    secret = settings.qr_signing_secret.encode("utf-8")
    expiry = int(time.time()) + (expiry_hours * 3600)
    parts = [tenant_id, department_id or "", branch_id or "", str(expiry)]
    payload = "|".join(parts)
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode("utf-8")
    return token


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
