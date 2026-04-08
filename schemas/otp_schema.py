from __future__ import annotations

from pydantic import BaseModel


class OtpVerifyRequest(BaseModel):
    """Step 2 of 2FA login — verify the OTP code."""
    otp_challenge_id: str
    otp_code: str


class OtpChallengeOut(BaseModel):
    """Returned from step 1 when OTP verification is required."""
    otp_required: bool = True
    otp_challenge_id: str
    message: str = "OTP verification required"


class MfaSettingsUpdate(BaseModel):
    """For a user toggling their own 2FA."""
    mfa_enabled: bool


class MfaAdminUpdate(BaseModel):
    """For super admin setting a user's 2FA + lock."""
    mfa_enabled: bool
    mfa_locked_by_admin: bool = False
