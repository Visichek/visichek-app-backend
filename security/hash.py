import hashlib
import hmac
import os
import secrets

import bcrypt

_OTP_SECRET = os.getenv("SECRET_KEY", "dev-otp-key").encode()

def hash_password(password: str|bytes) -> bytes: # type: ignore
    if type(password)==str:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed


 

def check_password(password: str, hashed: bytes | str) -> bool:
    # if hashed is string, convert to bytes
    if isinstance(hashed, str):
        hashed = hashed.encode('utf-8')
    return bcrypt.checkpw(password.encode('utf-8'), hashed)


def hash_otp(code: str) -> str:
    return hmac.HMAC(_OTP_SECRET, code.encode(), hashlib.sha256).hexdigest()


def check_otp(code: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_otp(code), hashed)


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
