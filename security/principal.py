from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


# Legacy roles (from FasterAPI boilerplate)
LEGACY_ROLES = ("user", "admin")

# VisiChek system user roles
SYSTEM_USER_ROLES = (
    "receptionist", "dept_admin", "super_admin",
    "auditor", "security_officer", "dpo",
)

ALL_ROLES = LEGACY_ROLES + SYSTEM_USER_ROLES

AllRolesLiteral = Literal[
    "user", "admin",
    "receptionist", "dept_admin", "super_admin",
    "auditor", "security_officer", "dpo",
]


class AuthPrincipal(BaseModel):
    user_id: str
    role: AllRolesLiteral
    access_token_id: str
    jwt_token: str
    allow_expired: bool = False
    tenant_id: Optional[str] = None

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
        return self.role in SYSTEM_USER_ROLES

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_receptionist(self) -> bool:
        return self.role == "receptionist"

    def has_role(self, *roles: str) -> bool:
        return self.role in roles
