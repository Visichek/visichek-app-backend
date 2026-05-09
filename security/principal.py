from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


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

# Tenant roles whose data visibility is limited to their assigned branch_ids.
# Other tenant roles (super_admin, auditor, dpo) see all branches in the tenant.
BRANCH_SCOPED_ROLES: tuple[str, ...] = (
    "dept_admin",
    "receptionist",
    "security_officer",
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
    # Branch assignments captured at token-issuance time. Branch-scoped roles
    # use this list to filter reads/writes; unscoped roles ignore it.
    branch_ids: List[str] = Field(default_factory=list)

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

    @property
    def is_branch_scoped(self) -> bool:
        """True when this principal can only see data from their branch_ids.

        Returns False for app-admin/app-user (no tenant) and for the unscoped
        tenant roles (super_admin, auditor, dpo) which see every branch.
        """
        return self.role in BRANCH_SCOPED_ROLES

    def branch_filter(self, field: str = "branch_id") -> Optional[dict[str, Any]]:
        """Return a Mongo filter fragment that scopes a query to this user's
        branches, or ``None`` if the principal sees everything.

        Use as: ``query.update(principal.branch_filter() or {})``.
        Returns ``{"branch_id": {"$in": [...]}}`` for branch-scoped principals,
        or ``{"branch_id": {"$in": []}}`` (which matches nothing) when a
        scoped user has no branches assigned — fail closed, never wide-open.
        """
        if not self.is_branch_scoped:
            return None
        return {field: {"$in": list(self.branch_ids)}}

    def has_role(self, *roles: str) -> bool:
        return self.role in roles
