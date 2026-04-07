"""Tests for static role permission configuration."""
from __future__ import annotations

import pytest

from config.role_permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    get_default_permissions_for_role,
    ADMIN_PERMISSIONS,
    USER_PERMISSIONS,
    SUPER_ADMIN_PERMISSIONS,
    DEPT_ADMIN_PERMISSIONS,
    RECEPTIONIST_PERMISSIONS,
    AUDITOR_PERMISSIONS,
    SECURITY_OFFICER_PERMISSIONS,
    DPO_PERMISSIONS,
)
from schemas.imports import Permission, PermissionList


ALL_ROLE_NAMES = [
    "admin",
    "user",
    "super_admin",
    "dept_admin",
    "receptionist",
    "auditor",
    "security_officer",
    "dpo",
]


class TestRolePermissionsConfig:
    """Ensure all 8 roles are defined and structurally valid."""

    def test_all_roles_present(self):
        for role in ALL_ROLE_NAMES:
            assert role in DEFAULT_ROLE_PERMISSIONS, f"Missing role: {role}"

    def test_no_extra_roles(self):
        assert set(DEFAULT_ROLE_PERMISSIONS.keys()) == set(ALL_ROLE_NAMES)

    @pytest.mark.parametrize("role", ALL_ROLE_NAMES)
    def test_role_has_permissions(self, role: str):
        perms = DEFAULT_ROLE_PERMISSIONS[role]
        assert len(perms) > 0, f"Role {role} has zero permissions"

    @pytest.mark.parametrize("role", ALL_ROLE_NAMES)
    def test_permissions_are_permission_instances(self, role: str):
        for p in DEFAULT_ROLE_PERMISSIONS[role]:
            assert isinstance(p, Permission), f"Role {role} has non-Permission item: {p}"

    @pytest.mark.parametrize("role", ALL_ROLE_NAMES)
    def test_permissions_have_required_fields(self, role: str):
        for p in DEFAULT_ROLE_PERMISSIONS[role]:
            assert p.name, f"Permission missing name in role {role}"
            assert p.methods, f"Permission {p.name} missing methods in role {role}"
            assert p.path, f"Permission {p.name} missing path in role {role}"
            assert p.key, f"Permission {p.name} missing key in role {role}"

    @pytest.mark.parametrize("role", ALL_ROLE_NAMES)
    def test_permission_keys_are_unique(self, role: str):
        keys = [p.key for p in DEFAULT_ROLE_PERMISSIONS[role]]
        assert len(keys) == len(set(keys)), f"Duplicate keys in role {role}: {[k for k in keys if keys.count(k) > 1]}"


class TestGetDefaultPermissionsForRole:
    """Test the get_default_permissions_for_role() helper."""

    @pytest.mark.parametrize("role", ALL_ROLE_NAMES)
    def test_returns_permission_list(self, role: str):
        result = get_default_permissions_for_role(role)
        assert isinstance(result, PermissionList)
        assert len(result.permissions) > 0

    def test_unknown_role_raises_key_error(self):
        with pytest.raises(KeyError, match="No default permissions defined"):
            get_default_permissions_for_role("nonexistent_role")


class TestRolePermissionScoping:
    """Verify role-specific access boundaries."""

    def _paths_for_role(self, role: str) -> set[str]:
        return {p.path for p in DEFAULT_ROLE_PERMISSIONS[role]}

    def test_receptionist_cannot_manage_users(self):
        paths = self._paths_for_role("receptionist")
        assert "/v1/system-users/signup" not in paths
        assert "/v1/system-users/{user_id}" not in paths

    def test_auditor_is_read_only(self):
        for p in AUDITOR_PERMISSIONS:
            assert set(p.methods).issubset({"GET", "POST"}), (
                f"Auditor has non-read method: {p.methods} on {p.path}"
            )

    def test_security_officer_only_incidents(self):
        paths = self._paths_for_role("security_officer")
        for path in paths:
            assert "incident" in path or "me" in path, (
                f"Security officer has unexpected path: {path}"
            )

    def test_admin_has_tenant_bootstrap(self):
        paths = self._paths_for_role("admin")
        assert "/v1/admins/tenants/bootstrap" in paths

    def test_super_admin_has_branch_management(self):
        paths = self._paths_for_role("super_admin")
        assert "/v1/branches" in paths

    def test_all_tenant_roles_can_view_own_profile(self):
        tenant_roles = ["super_admin", "dept_admin", "receptionist", "auditor", "security_officer", "dpo"]
        for role in tenant_roles:
            paths = self._paths_for_role(role)
            assert "/v1/system-users/me" in paths, f"Role {role} cannot view own profile"
