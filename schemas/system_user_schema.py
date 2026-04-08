from schemas.imports import *
from pydantic import Field
import time
from security.hash import hash_password


class SystemUserBase(BaseModel):
    tenant_id: str
    department_id: Optional[str] = None
    full_name: str
    email: EmailStr
    role: SystemUserRole
    account_status: AccountStatus = AccountStatus.ACTIVE
    is_active: bool = True
    last_login_at: Optional[int] = None
    permissionList: Optional[PermissionList] = None
    mfa_enabled: bool = False
    mfa_locked_by_admin: bool = False


class SystemUserSignupRequest(BaseModel):
    """Public-facing invite request. No account_status or permission_list —
    those are system-assigned based on role defaults."""

    department_id: Optional[str] = None
    full_name: str
    email: EmailStr
    password: str
    role: SystemUserRole


class SystemUserTenantLogin(BaseModel):
    """Login request scoped to a specific tenant (via URL path param)."""
    email: EmailStr
    password: str


class SystemUserCreate(SystemUserBase):
    """Internal creation schema. Built by the service layer, NOT exposed to clients."""

    password_hash: str | bytes
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_and_hash_password(self):
        if isinstance(self.password_hash, str):
            from security.password_policy import validate_password_strength
            result = validate_password_strength(self.password_hash)
            if not result.is_valid:
                raise ValueError("; ".join(result.errors))
        self.password_hash = hash_password(self.password_hash)
        return self


class SystemUserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    department_id: Optional[str] = None
    role: Optional[SystemUserRole] = None
    account_status: Optional[AccountStatus] = None
    is_active: Optional[bool] = None
    last_login_at: Optional[int] = None
    mfa_enabled: Optional[bool] = None
    mfa_locked_by_admin: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class SystemUserLogin(BaseModel):
    email: EmailStr
    password: str


class SystemUserOut(SystemUserBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None

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


class SystemUserRefresh(BaseModel):
    refresh_token: str
