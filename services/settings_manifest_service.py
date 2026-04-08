from __future__ import annotations

import os

from bson import ObjectId
from typing import Any

from core.database import db
from security.principal import AuthPrincipal, TENANT_USER_ROLES

PRIMARY_ADMIN_ID = "656f7ac12b9d4f6c9e2b9f7d"


def _is_primary_admin(principal: AuthPrincipal) -> bool:
    """Check if this is the .env-configured primary platform admin."""
    primary_email = os.getenv("SUPER_ADMIN_EMAIL")
    return (
        principal.role == "admin"
        and (principal.user_id == PRIMARY_ADMIN_ID or primary_email is not None)
    )


async def _is_primary_admin_by_email(principal: AuthPrincipal) -> bool:
    """Definitive check — compare the principal's email against SUPER_ADMIN_EMAIL."""
    if principal.role != "admin":
        return False
    if principal.user_id == PRIMARY_ADMIN_ID:
        return True
    primary_email = os.getenv("SUPER_ADMIN_EMAIL")
    if not primary_email:
        return False
    doc = await db.admins.find_one({"_id": ObjectId(principal.user_id)}, {"email": 1})
    if not doc:
        # Could be the hardcoded admin (no DB record)
        return principal.user_id == PRIMARY_ADMIN_ID
    return doc.get("email", "").lower() == primary_email.lower()


def _user_type(principal: AuthPrincipal) -> str:
    return "system_user" if principal.role in TENANT_USER_ROLES else "admin"


async def build_settings_manifest(principal: AuthPrincipal) -> dict[str, Any]:
    """Build a complete settings manifest for the authenticated user.

    Returns what the user can see, what they can change, which endpoints
    to call, and any enforcement flags that affect rendering.
    """
    user_type = _user_type(principal)
    role = principal.role
    is_primary = await _is_primary_admin_by_email(principal)

    # ── Load the user's profile ─────────────────────────────────────
    if user_type == "admin":
        doc = await db.admins.find_one({"_id": ObjectId(principal.user_id)})
        if not doc:
            # Hardcoded primary admin
            primary_email = os.getenv("SUPER_ADMIN_EMAIL", "")
            profile = {
                "id": PRIMARY_ADMIN_ID,
                "full_name": "Super Admin",
                "email": primary_email,
                "role": "admin",
                "account_status": "ACTIVE",
                "mfa_enabled": True,
            }
        else:
            profile = {
                "id": str(doc["_id"]),
                "full_name": doc.get("full_name", ""),
                "email": doc.get("email", ""),
                "role": "admin",
                "account_status": doc.get("accountStatus", "ACTIVE"),
                "mfa_enabled": doc.get("mfa_enabled", True),
            }
    else:
        doc = await db.system_users.find_one({"_id": ObjectId(principal.user_id)})
        if not doc:
            profile = {
                "id": principal.user_id,
                "full_name": "",
                "email": "",
                "role": role,
                "account_status": "ACTIVE",
                "mfa_enabled": False,
            }
        else:
            profile = {
                "id": str(doc["_id"]),
                "full_name": doc.get("full_name", ""),
                "email": doc.get("email", ""),
                "role": doc.get("role", role),
                "tenant_id": doc.get("tenant_id"),
                "department_id": doc.get("department_id"),
                "account_status": doc.get("account_status", "ACTIVE"),
                "mfa_enabled": doc.get("mfa_enabled", False),
                "mfa_locked_by_admin": doc.get("mfa_locked_by_admin", False),
            }

    # ── Check tenant-level 2FA enforcement ──────────────────────────
    tenant_enforces_totp = False
    mfa_user_override_allowed = True
    if principal.tenant_id:
        tenant = await db.tenant_companies.find_one({"_id": ObjectId(principal.tenant_id)})
        if tenant:
            tenant_enforces_totp = tenant.get("mfa_default_for_users", False)
            mfa_user_override_allowed = tenant.get("mfa_user_override_allowed", True)

        # Also check tenant_settings collection
        ts = await db["tenant_settings"].find_one({"tenant_id": principal.tenant_id})
        if ts and ts.get("enforce_totp"):
            tenant_enforces_totp = True

    # ── 2FA enforcement logic ───────────────────────────────────────
    mfa_required = False
    mfa_can_disable = True

    if is_primary:
        # Primary admin MUST have 2FA — cannot disable
        mfa_required = True
        mfa_can_disable = False
    elif user_type == "admin":
        # All admins have mfa_enabled=True by default but can manage it
        mfa_required = True
        mfa_can_disable = False
    elif tenant_enforces_totp:
        # Tenant policy forces 2FA
        mfa_required = True
        mfa_can_disable = False
    elif profile.get("mfa_locked_by_admin"):
        # Super admin locked this user's MFA
        mfa_can_disable = False

    # ── Account deletion eligibility ────────────────────────────────
    can_delete_account = True
    delete_blocked_reason = None

    if is_primary:
        can_delete_account = False
        delete_blocked_reason = "The primary platform admin account cannot be deleted."
    elif role == "super_admin" and principal.tenant_id:
        # Check if sole super admin
        sa_count = await db.system_users.count_documents({
            "tenant_id": principal.tenant_id,
            "role": "super_admin",
            "account_status": "ACTIVE",
        })
        if sa_count <= 1:
            can_delete_account = False
            delete_blocked_reason = "You are the sole super admin. Transfer ownership before deleting."

        # Check active subscription
        if can_delete_account:
            active_sub = await db["subscriptions"].find_one({
                "tenant_id": principal.tenant_id,
                "status": {"$in": ["active", "trialing"]},
            })
            if active_sub:
                can_delete_account = False
                delete_blocked_reason = "Cancel the active subscription before deleting your account."

    # ── Build sections ──────────────────────────────────────────────

    sections: list[dict[str, Any]] = []

    # 1. Profile
    sections.append({
        "key": "profile",
        "label": "Profile",
        "description": "Your personal information.",
        "fields": _profile_fields(user_type, role),
        "endpoints": {
            "get": f"/v1/{'admins' if user_type == 'admin' else 'system-users'}/profile"
                   if user_type == "admin" else "/v1/system-users/me",
        },
    })

    # 2. Appearance & Preferences
    sections.append({
        "key": "preferences",
        "label": "Preferences",
        "description": "Theme, language, timezone, and notification settings.",
        "endpoints": {
            "get": "/v1/user-settings",
            "update": "/v1/user-settings",
        },
    })

    # 3. Notifications
    sections.append({
        "key": "notifications",
        "label": "Notification Preferences",
        "description": "Control which notifications you receive and how.",
        "endpoints": {
            "get": "/v1/notifications/preferences",
            "update": "/v1/notifications/preferences",
        },
    })

    # 4. Security — Password
    sections.append({
        "key": "password",
        "label": "Password",
        "description": "Change your password. Must meet the platform password policy.",
        "endpoints": {
            "change": "/v1/auth/change-password",
        },
    })

    # 5. Security — 2FA
    sections.append({
        "key": "two_factor",
        "label": "Two-Factor Authentication",
        "description": "Secure your account with TOTP-based two-factor authentication.",
        "current_state": {
            "enabled": profile.get("mfa_enabled", False),
            "required": mfa_required,
            "can_disable": mfa_can_disable,
            "enforcement_reason": (
                "Required for all platform admins." if (is_primary or user_type == "admin")
                else "Enforced by tenant policy." if tenant_enforces_totp
                else "Locked by your organization admin." if profile.get("mfa_locked_by_admin")
                else None
            ),
        },
        "endpoints": {
            "setup": "/v1/auth/2fa/setup",
            "verify": "/v1/auth/2fa/verify",
            "disable": "/v1/auth/2fa",
            "regenerate_backup_codes": "/v1/auth/2fa/backup-codes/regenerate",
        },
    })

    # 6. Sessions
    sections.append({
        "key": "sessions",
        "label": "Active Sessions",
        "description": "View and manage your active login sessions.",
        "endpoints": {
            "list": "/v1/sessions",
            "revoke": "/v1/sessions/{session_id}",
            "revoke_all": "/v1/sessions/revoke-all",
        },
    })

    # 7. Dashboard Preferences (key-value store)
    sections.append({
        "key": "dashboard_preferences",
        "label": "Dashboard Layout",
        "description": "Persist dashboard quick action order and collapsed sections.",
        "endpoints": {
            "get": f"/v1/{'admins' if user_type == 'admin' else 'system-users'}/preferences",
            "update": f"/v1/{'admins' if user_type == 'admin' else 'system-users'}/preferences",
        },
    })

    # 8. Account Deletion
    sections.append({
        "key": "account_deletion",
        "label": "Delete Account",
        "description": "Permanently deactivate your account.",
        "allowed": can_delete_account,
        "blocked_reason": delete_blocked_reason,
        "endpoints": {
            "delete": "/v1/account",
        },
    })

    # 9. Tenant Settings (super_admin only)
    if role == "super_admin":
        sections.append({
            "key": "tenant_settings",
            "label": "Organization Settings",
            "description": "Security policies, visitor policies, data retention, and integrations for your organization.",
            "endpoints": {
                "get": "/v1/tenant-settings",
                "update": "/v1/tenant-settings",
            },
        })

    # 10. Platform Settings (admin only — primary admin gets full access)
    if user_type == "admin":
        sections.append({
            "key": "platform_settings",
            "label": "Platform Settings",
            "description": "Global platform configuration, feature flags, SMTP, and rate limits.",
            "readonly": not is_primary,
            "is_primary_admin": is_primary,
            "endpoints": {
                "get": "/v1/platform-settings",
                "update": "/v1/platform-settings" if is_primary else None,
            },
        })

    return {
        "profile": profile,
        "is_primary_admin": is_primary,
        "sections": sections,
    }


def _profile_fields(user_type: str, role: str) -> list[dict[str, Any]]:
    """Return the editable profile fields for this user type."""
    fields = [
        {"key": "full_name", "label": "Full Name", "type": "text", "editable": True},
        {"key": "email", "label": "Email", "type": "email", "editable": False},
    ]

    if user_type == "system_user":
        fields.append({"key": "role", "label": "Role", "type": "text", "editable": False})
        fields.append({"key": "department_id", "label": "Department", "type": "text", "editable": False})

    return fields
