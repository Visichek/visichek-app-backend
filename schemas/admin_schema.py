from schemas.imports import *
from pydantic import Field
import time
from security.hash import hash_password
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator


# Application-admin access preset (Issue 10 backend).
#
# Five scope presets the invite flow accepts. Each one maps to a
# slice of ``ADMIN_PERMISSIONS`` via
# ``config.role_permissions.get_default_permissions_for_admin_preset``.
# ``all_controls`` is the legacy full-access default — kept for
# backwards compatibility with admins provisioned before presets
# shipped.
AdminAccessPreset = Literal[
    "content_only",
    "support_only",
    "content_support",
    "billing_only",
    "all_controls",
]


class AdminBase(BaseModel):
    """Base fields for admin display/output. NOT used for creation input."""

    full_name: str
    email: EmailStr
    password: Optional[str | bytes] = None
    accountStatus: AccountStatus = AccountStatus.ACTIVE
    permissionList: Optional[PermissionList] = None
    mfa_enabled: bool = True
    # Issue 10 backend: persisted access preset. Optional for
    # backwards compatibility — legacy admins without a preset are
    # treated as ``all_controls`` by
    # ``get_default_permissions_for_admin_preset``.
    access_preset: Optional[AdminAccessPreset] = None
    # True when the row's current password is a system-generated
    # temporary value (set on every admin invite, since the inviting
    # admin no longer chooses the password — see
    # ``security/password_policy.generate_secure_temp_password``).
    # The admin gate (``check_admin_account_status_and_permissions``)
    # refuses every endpoint except the change-password ones until
    # the flag is cleared by a successful self-change.
    must_change_password: bool = False

    # When the temporary password was issued. Bounds its life — see
    # ``security/temp_password.py``. None on legacy rows (grandfathered) and on
    # accounts that aren't on a temp password at all.
    must_change_password_at: Optional[int] = None


class AdminLogin(BaseModel):
    email: EmailStr
    password: str | bytes

    @field_validator("password", mode="before")
    @classmethod
    def strip_password_whitespace(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class AdminRefresh(BaseModel):
    refresh_token: str


class AdminSignupRequest(BaseModel):
    """Public-facing signup/invite request. No account_status or permission_list —
    those are system-assigned based on role defaults.

    Issue 10 backend: ``access_preset`` is optional on signup. The
    inviting admin (must have ``all_controls``) chooses the scope for
    the new account. Omitted → defaults to ``all_controls`` for
    backwards compatibility with any direct API callers that haven't
    adopted the field yet.

    The new admin's password is ALWAYS system-generated — the inviter
    does not (and cannot) choose it. The cleartext value is emailed to
    the new admin (via the ``admin_invite`` template) and never returned
    in the API response, and the row is marked
    ``must_change_password=true`` so the new admin must pick their own
    password on first sign-in.
    """

    full_name: str
    email: EmailStr
    access_preset: Optional[AdminAccessPreset] = None


class AdminCreate(AdminBase):
    """Internal creation schema. Built by the service layer, NOT exposed to clients."""

    invited_by: str
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_and_hash_password(self):
        from security.password_policy import validate_password_strength

        if isinstance(self.password, str):
            result = validate_password_strength(self.password)
            if not result.is_valid:
                raise ValueError("; ".join(result.errors))
        self.password = hash_password(self.password)
        return self


class AdminUpdate(BaseModel):
    password: Optional[str | bytes] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_and_hash_password(self):
        if self.password and isinstance(self.password, str):
            from security.password_policy import validate_password_strength

            result = validate_password_strength(self.password)
            if not result.is_valid:
                raise ValueError("; ".join(result.errors))
            self.password = hash_password(self.password)
        return self


class AdminOut(AdminBase):
    id: Optional[str] = Field(default=None, alias="_id")
    password: Optional[str | bytes] = Field(default=None, exclude=True)

    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class AdminSearchResult(BaseModel):
    """Compact, human-readable admin record for search results.

    Strips password, tokens, and the full permission list — callers
    only need enough to identify and route to the admin.
    """

    id: str
    full_name: str
    email: EmailStr
    account_status: AccountStatus
    mfa_enabled: bool = True
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @classmethod
    def from_admin_out(cls, admin: "AdminOut") -> "AdminSearchResult":
        return cls(
            id=admin.id or "",
            full_name=admin.full_name,
            email=admin.email,
            account_status=admin.accountStatus,
            mfa_enabled=admin.mfa_enabled,
            date_created=admin.date_created,
            last_updated=admin.last_updated,
        )
