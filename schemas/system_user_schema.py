from schemas.imports import *
from pydantic import Field, field_validator
import time
from security.hash import hash_password


class SystemUserBase(BaseModel):
    tenant_id: str
    department_id: Optional[str] = None
    # Every system user is assigned to at least one branch within their tenant.
    # For branch-scoped roles (dept_admin, receptionist, security_officer) this
    # restricts the data they can read/write. For unscoped roles (super_admin,
    # auditor, dpo) it identifies their primary branch but does NOT filter access.
    branch_ids: List[str] = Field(default_factory=list)
    full_name: str
    email: EmailStr
    role: SystemUserRole
    account_status: AccountStatus = AccountStatus.ACTIVE
    is_active: bool = True
    last_login_at: Optional[int] = None
    permissionList: Optional[PermissionList] = None
    mfa_enabled: bool = False
    mfa_locked_by_admin: bool = False
    # Exactly one super_admin per tenant carries the "main" flag — it is the
    # permanent owner of the tenant. Enforced by:
    #   * the partial-unique index in core/indexes.py keyed on
    #     ``(tenant_id, is_main_super_admin=True)``,
    #   * the bootstrap / add-super_admin paths which set the flag when the
    #     tenant has zero existing super_admins,
    #   * the mutation guard in services/main_super_admin_guard.py which
    #     rejects role / account_status / is_active changes on the main row,
    #   * the lifespan + 6h APScheduler invariant check
    #     (services/main_super_admin_backfill.py) which auto-heals drift
    #     and opens a high-priority support case for tenants with zero
    #     active super_admins.
    # Use the dedicated transfer endpoint to move the flag — never $set it
    # directly from a writer.
    is_main_super_admin: bool = False


class SystemUserSignupRequest(BaseModel):
    """Public-facing invite request. No account_status or permission_list —
    those are system-assigned based on role defaults."""

    department_id: Optional[str] = None
    # Optional on the wire: when omitted (or empty) the service layer defaults
    # to the tenant's headquarters branch. When provided, it must be a subset of
    # the tenant's branches AND respect the plan's max_branches cap.
    branch_ids: Optional[List[str]] = None
    full_name: str
    email: EmailStr
    password: str
    role: SystemUserRole


class SystemUserTenantLogin(BaseModel):
    """Login request scoped to a specific tenant (via URL path param)."""

    email: EmailStr
    password: str

    @field_validator("password", mode="before")
    @classmethod
    def strip_password_whitespace(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


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
    # When set, replaces the user's branch assignments. Must be non-empty and
    # the service layer enforces tenant-membership + plan cap.
    branch_ids: Optional[List[str]] = None
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

    @field_validator("password", mode="before")
    @classmethod
    def strip_password_whitespace(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


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


class TenantProfileSummary(BaseModel):
    """Lightweight tenant snapshot embedded in the system user profile response."""

    id: Optional[str] = None
    company_name: Optional[str] = None
    lawful_basis: Optional[str] = None
    notice_display_mode: Optional[str] = None
    dpo_contact_email: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    country_of_hosting: Optional[str] = None
    cross_border_approved: Optional[bool] = None
    is_active: Optional[bool] = None
    enable_repeat_visitor_recognition: Optional[bool] = None
    mfa_default_for_users: Optional[bool] = None
    mfa_user_override_allowed: Optional[bool] = None


class SystemUserProfileOut(SystemUserOut):
    """SystemUserOut enriched with tenant context for frontend rendering."""

    tenant: Optional[TenantProfileSummary] = None


class TenantOption(BaseModel):
    """One tenant the user can pick from when their email matches multiple."""

    tenant_id: str
    company_name: Optional[str] = None
    role: SystemUserRole
    full_name: str
    mfa_enabled: bool = False


class TenantSelectionResponse(BaseModel):
    """Stage 1 response when an email matches more than one tenant."""

    tenant_selection_required: bool = True
    selection_token: str
    tenants: list[TenantOption]


class TenantSelectionRequest(BaseModel):
    """Stage 2 request — user picks which tenant to log into."""

    selection_token: str
    tenant_id: str


# --- Main super_admin transfer ------------------------------------------------


class TransferMainSuperAdminInitiateRequest(BaseModel):
    """Step 1 of the main super_admin transfer.

    Validates the target + actor permissions, mints an OTP challenge that
    must be completed via :class:`TransferMainSuperAdminVerifyRequest`.
    ``tenant_id`` is required only when the caller is an application admin
    operating cross-tenant; tenant super_admins infer it from their token.
    """

    new_main_super_admin_user_id: str
    tenant_id: Optional[str] = None


class TransferMainSuperAdminInitiateResponse(BaseModel):
    """Step 1 response — carries the challenge id the FE submits to step 2."""

    otp_required: bool = True
    otp_challenge_id: str
    new_main_super_admin_user_id: str
    tenant_id: str
    message: str = (
        "Verification code sent. Submit the code to /v1/system-users/"
        "transfer-main-super-admin to complete the transfer."
    )


class TransferMainSuperAdminVerifyRequest(BaseModel):
    """Step 2 of the main super_admin transfer.

    The challenge id binds the OTP to the actor + intended target. The
    body must re-state the target so a stolen challenge id cannot be
    swapped to a different super_admin. Mismatch → 400 ``OTP_TARGET_MISMATCH``.
    """

    otp_challenge_id: str
    otp_code: str
    new_main_super_admin_user_id: str
    tenant_id: Optional[str] = None
