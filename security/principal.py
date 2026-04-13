from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


# Application-level roles (platform operators)
# - "admin" = Application Admin — manages tenants, plans, subscriptions
# - "user"  = Application User  — original boilerplate user role
APP_ROLES = ("user", "admin")

# Tenant-scoped roles (VisiChek system users within a tenant)
# - "super_admin"      = Tenant Super Admin — manages tenant config, branches, users
# - "dept_admin"       = Department Admin   — manages a single department
# - "receptionist"     = Front-desk staff   — check-in/out visitors
# - "auditor"          = Reads audit logs
# - "security_officer" = Manages incidents
# - "dpo"              = Data Protection Officer
TENANT_USER_ROLES = (
    "receptionist",
    "dept_admin",
    "super_admin",
    "auditor",
    "security_officer",
    "dpo",
)

ALL_ROLES = APP_ROLES + TENANT_USER_ROLES

AllRolesLiteral = Literal[
    "user",
    "admin",
    "receptionist",
    "dept_admin",
    "super_admin",
    "auditor",
    "security_officer",
    "dpo",
]


class AuthPrincipal(BaseModel):
    user_id: str
    role: AllRolesLiteral
    access_token_id: str
    jwt_token: str
    allow_expired: bool = False
    tenant_id: Optional[str] = None
    department_id: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_user(self) -> bool:
        return self.role == "user"

    @property
    def is_member(self) -> bool:
        return self.role == "user"

    @property
    def is_system_user(self) -> bool:
        return self.role in TENANT_USER_ROLES

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_receptionist(self) -> bool:
        return self.role == "receptionist"

    def has_role(self, *roles: str) -> bool:
        return self.role in roles
