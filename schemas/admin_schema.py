from schemas.imports import *
from pydantic import Field
import time
from security.hash import hash_password
from typing import Optional
from pydantic import BaseModel, EmailStr, model_validator


class AdminBase(BaseModel):
    """Base fields for admin display/output. NOT used for creation input."""

    full_name: str
    email: EmailStr
    password: Optional[str | bytes] = None
    accountStatus: AccountStatus = AccountStatus.ACTIVE
    permissionList: Optional[PermissionList] = None
    mfa_enabled: bool = True


class AdminLogin(BaseModel):
    email: EmailStr
    password: str | bytes


class AdminRefresh(BaseModel):
    refresh_token: str


class AdminSignupRequest(BaseModel):
    """Public-facing signup/invite request. No account_status or permission_list —
    those are system-assigned based on role defaults."""

    full_name: str
    email: EmailStr
    password: str


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
