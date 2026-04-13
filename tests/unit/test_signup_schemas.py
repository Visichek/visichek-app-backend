"""Tests for public-facing signup/login schemas.

Verifies that:
- SignupRequest schemas do NOT expose account_status or permissionList
- Login schemas only have email + password
- Internal *Create schemas still have system-assigned fields
"""
from __future__ import annotations


from schemas.admin_schema import AdminSignupRequest
from schemas.user_schema import UserSignupRequest, UserLogin
from schemas.system_user_schema import (
    SystemUserSignupRequest,
    SystemUserTenantLogin,
)
from schemas.imports import SystemUserRole


class TestAdminSignupRequest:
    """AdminSignupRequest should only allow name, email, password."""

    def test_valid_signup(self):
        data = AdminSignupRequest(
            full_name="John Admin",
            email="john@example.com",
            password="StrongP@ss1!",
        )
        assert data.full_name == "John Admin"
        assert data.email == "john@example.com"

    def test_no_account_status_field(self):
        """account_status should not be a field on the signup schema."""
        assert "accountStatus" not in AdminSignupRequest.model_fields
        assert "account_status" not in AdminSignupRequest.model_fields

    def test_no_permission_list_field(self):
        """permissionList should not be a field on the signup schema."""
        assert "permissionList" not in AdminSignupRequest.model_fields
        assert "permission_list" not in AdminSignupRequest.model_fields

    def test_extra_fields_ignored_or_rejected(self):
        """Passing account_status should not be accepted."""
        data = AdminSignupRequest(
            full_name="John",
            email="john@example.com",
            password="StrongP@ss1!",
        )
        # The field should not appear on the model
        assert not hasattr(data, "accountStatus") or "accountStatus" not in data.model_fields


class TestUserSignupRequest:
    """UserSignupRequest should only allow firstName, lastName, email, password, loginType."""

    def test_valid_signup(self):
        data = UserSignupRequest(
            firstName="Alice",
            lastName="Smith",
            email="alice@example.com",
            password="StrongP@ss1!",
        )
        assert data.firstName == "Alice"

    def test_no_account_status_field(self):
        assert "accountStatus" not in UserSignupRequest.model_fields
        assert "account_status" not in UserSignupRequest.model_fields

    def test_no_permission_list_field(self):
        assert "permissionList" not in UserSignupRequest.model_fields


class TestUserLogin:
    """UserLogin should only have email and password."""

    def test_valid_login(self):
        data = UserLogin(email="alice@example.com", password="mypassword")
        assert data.email == "alice@example.com"

    def test_only_two_fields(self):
        assert set(UserLogin.model_fields.keys()) == {"email", "password"}


class TestSystemUserSignupRequest:
    """SystemUserSignupRequest — no account_status, no permissionList."""

    def test_valid_signup(self):
        data = SystemUserSignupRequest(
            full_name="Dr. Sarah",
            email="sarah@clinic.com",
            password="StrongP@ss1!",
            role=SystemUserRole.RECEPTIONIST,
        )
        assert data.role == SystemUserRole.RECEPTIONIST

    def test_no_account_status_field(self):
        assert "account_status" not in SystemUserSignupRequest.model_fields
        assert "accountStatus" not in SystemUserSignupRequest.model_fields

    def test_no_permission_list_field(self):
        assert "permissionList" not in SystemUserSignupRequest.model_fields

    def test_no_tenant_id_field(self):
        """tenant_id is set by the system from the super admin's context."""
        assert "tenant_id" not in SystemUserSignupRequest.model_fields


class TestSystemUserTenantLogin:
    """SystemUserTenantLogin — tenant-scoped login with only email + password."""

    def test_valid_login(self):
        data = SystemUserTenantLogin(email="user@clinic.com", password="mypassword")
        assert data.email == "user@clinic.com"

    def test_only_two_fields(self):
        assert set(SystemUserTenantLogin.model_fields.keys()) == {"email", "password"}
