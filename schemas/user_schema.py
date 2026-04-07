from schemas.imports import *
from pydantic import Field
import time
from security.hash import hash_password


class UserBase(BaseModel):
    """Base fields for user display/output."""

    firstName: str
    lastName: str
    loginType: LoginType
    email: EmailStr
    password: str | bytes
    accountStatus: AccountStatus = AccountStatus.ACTIVE
    permissionList: Optional[PermissionList] = None


class UserSignupRequest(BaseModel):
    """Public-facing signup request. No account_status or permission_list."""

    firstName: str
    lastName: str
    email: EmailStr
    password: str
    loginType: LoginType = LoginType.email


class UserLogin(BaseModel):
    """Login request — only email and password."""

    email: EmailStr
    password: str


class UserRefresh(BaseModel):
    refresh_token: str


class UserCreate(UserBase):
    """Internal creation schema. Built by the service layer."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode='after')
    def validate_and_hash_password(self):
        from security.password_policy import validate_password_strength
        if isinstance(self.password, str):
            result = validate_password_strength(self.password)
            if not result.is_valid:
                raise ValueError("; ".join(result.errors))
        self.password = hash_password(self.password)
        return self


class UserUpdate(BaseModel):
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class UserOut(UserBase):
    id: Optional[str] = Field(default=None, alias="_id")
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
