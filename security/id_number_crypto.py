from __future__ import annotations

import base64

from cryptography.fernet import Fernet

from core.settings import get_settings


def encrypt_id_number(plaintext: str) -> str:
    """Encrypt an ID number using AEAD encryption."""
    settings = get_settings()
    if not settings.id_number_encryption_key:
        raise RuntimeError("ID_NUMBER_ENCRYPTION_KEY not configured")

    key_bytes = base64.b64decode(settings.id_number_encryption_key)
    cipher = Fernet(key_bytes)
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext_bytes = cipher.encrypt(plaintext_bytes)
    return base64.b64encode(ciphertext_bytes).decode("utf-8")


def decrypt_id_number(ciphertext: str) -> str:
    """Decrypt an ID number using AEAD encryption."""
    settings = get_settings()
    if not settings.id_number_encryption_key:
        raise RuntimeError("ID_NUMBER_ENCRYPTION_KEY not configured")

    key_bytes = base64.b64decode(settings.id_number_encryption_key)
    cipher = Fernet(key_bytes)
    ciphertext_bytes = base64.b64decode(ciphertext.encode("utf-8"))
    plaintext_bytes = cipher.decrypt(ciphertext_bytes)
    return plaintext_bytes.decode("utf-8")
