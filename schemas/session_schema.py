from __future__ import annotations

from schemas.imports import *


# --- Enums ---


class DeviceType(str, Enum):
    DESKTOP = "desktop"
    TABLET = "tablet"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


# --- Session ---


class SessionBase(BaseModel):
    """An active user session."""

    user_id: str
    user_type: str  # "admin" or "system_user"
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_type: DeviceType = DeviceType.UNKNOWN
    device: Optional[str] = None  # Human-readable label e.g. "Chrome (Windows)"
    location: Optional[str] = None
    is_current: bool = False


class SessionCreate(SessionBase):
    """Internal creation schema."""

    access_token_id: str
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_active_at: int = Field(default_factory=lambda: int(time.time()))


class SessionOut(SessionBase):
    """Response schema for a session."""

    id: Optional[str] = Field(default=None, alias="_id")
    access_token_id: Optional[str] = None
    date_created: Optional[int] = None
    last_active_at: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


# --- Password Change ---


class ChangePasswordRequest(BaseModel):
    """Request body for changing a password."""

    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    """Request body for an authority-driven password reset.

    Used by:
      * Application admin → /v1/admins/system-users/{user_id}/reset-password
      * Tenant super_admin → /v1/system-users/{user_id}/reset-password

    The new password is ALWAYS system-generated — actors do not (and
    cannot) choose it. The cleartext value is emailed to the target
    user and never returned in the API response, and the row is marked
    ``must_change_password=true`` so the target must pick their own
    password on the next sign-in. Body is intentionally empty so the
    POST is a pure action confirmation.
    """

    pass


class AddSuperAdminRequest(BaseModel):
    """Request body for an application admin adding a super_admin to an
    existing tenant (separate from the bootstrap path which creates the
    tenant + first super_admin together).

    The new super_admin's password is ALWAYS system-generated — admins
    do not (and cannot) choose it. The cleartext value is emailed to
    the new super_admin and never returned in the API response, and the
    row is marked ``must_change_password=true`` so the user must pick
    their own password on first login.
    """

    full_name: str
    email: EmailStr
    # Optional branch assignment; defaults to the tenant's headquarters
    # branch when omitted. Multi-branch requires the plan's max_branches > 1.
    branch_ids: Optional[List[str]] = None


class ReplaceSuperAdminRequest(BaseModel):
    """Request body for an application admin replacing a tenant's sole
    super_admin with a new one.

    A tenant may only have one active super_admin at a time, so the
    standard ``AddSuperAdminRequest`` is rejected when one already
    exists. This endpoint atomically deactivates the existing main
    super_admin and provisions a new one in its place.

    The new super_admin's password is ALWAYS system-generated — admins
    do not (and cannot) choose it. The cleartext value is emailed to
    the new super_admin and never returned in the API response, and the
    row is marked ``must_change_password=true`` so the user must pick
    their own password on first login.
    """

    full_name: str
    email: EmailStr
    branch_ids: Optional[List[str]] = None


# --- Two-Factor Authentication ---


class TwoFactorSetupOut(BaseModel):
    """Returned when initiating 2FA setup."""

    secret: str
    qr_code_uri: str
    backup_codes: List[str]


class TwoFactorVerifyRequest(BaseModel):
    """Verify a TOTP code or backup code."""

    code: str


class TwoFactorAuthenticateRequest(BaseModel):
    """Step 2 of 2FA login — verify code with temp token."""

    temp_token: str
    code: str


class TwoFactorDisableRequest(BaseModel):
    """Disable 2FA — requires password for confirmation."""

    password: str


class BackupCodesRegenerateRequest(BaseModel):
    """Regenerate backup codes — requires password for confirmation."""

    password: str


class AccountDeleteRequest(BaseModel):
    """Request account deletion — requires password confirmation."""

    password: str


# --- Forgot / reset password (unauthenticated) ---


class ForgotPasswordRequest(BaseModel):
    """Public request body for POST /v1/auth/forgot-password."""

    email: EmailStr


class ResetPasswordWithTokenRequest(BaseModel):
    """Public request body for POST /v1/auth/reset-password.

    The ``token`` is the single-use opaque string sent in the password
    reset email — the server stores only ``sha256(token)``.
    """

    token: str
    new_password: str
