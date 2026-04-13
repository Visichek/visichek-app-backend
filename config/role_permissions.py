"""
Default Role Permission Configuration
======================================

Defines the default permission list each role receives on account creation.
These are the *maximum* permissions for the role — the plan enforcement
middleware further restricts access based on the tenant's active subscription.

Permission Format:
    Permission(name, methods, path, key, description)

- ``path`` uses FastAPI path patterns (e.g. ``/v1/visitors/*``)
- ``methods`` lists allowed HTTP methods
- ``key`` is a unique identifier for the permission (method:path)

To modify permissions for a role, edit the corresponding list below.
No database queries needed — this is static, version-controlled config.
"""

from __future__ import annotations

from schemas.imports import Permission, PermissionList


# ---------------------------------------------------------------------------
# Helper to build Permission objects quickly
# ---------------------------------------------------------------------------


def _p(name: str, methods: list[str], path: str, description: str = "") -> Permission:
    key = f"{','.join(methods)}:{path}"
    return Permission(
        name=name, methods=methods, path=path, key=key, description=description
    )


# ---------------------------------------------------------------------------
# Application Admin — full platform access
# ---------------------------------------------------------------------------

ADMIN_PERMISSIONS: list[Permission] = [
    # Tenant management
    _p("list_admins", ["GET"], "/v1/admins/", "List all application admins"),
    _p("get_admin_profile", ["GET"], "/v1/admins/profile", "View own admin profile"),
    _p("create_admin", ["POST"], "/v1/admins/signup", "Invite a new application admin"),
    _p("delete_admin", ["DELETE"], "/v1/admins/account", "Delete own admin account"),
    _p(
        "bootstrap_tenant",
        ["POST"],
        "/v1/admins/tenants/bootstrap",
        "Bootstrap tenant + first super admin",
    ),
    _p(
        "admin_dashboard",
        ["GET"],
        "/v1/admins/dashboard/stats",
        "View platform-wide dashboard",
    ),
    # Tenant CRUD
    _p("create_tenant", ["POST"], "/v1/tenants/", "Create a tenant"),
    _p("list_tenants", ["GET"], "/v1/tenants/", "List all tenants"),
    _p("get_tenant", ["GET"], "/v1/tenants/{tenant_id}", "View tenant details"),
    _p("update_tenant", ["PATCH"], "/v1/tenants/{tenant_id}", "Update tenant settings"),
    # Plan management
    _p("create_plan", ["POST"], "/v1/plans", "Create a subscription plan"),
    _p("list_plans", ["GET"], "/v1/plans", "List plans"),
    _p("get_plan", ["GET"], "/v1/plans/{plan_id}", "View plan details"),
    _p("update_plan", ["PUT"], "/v1/plans/{plan_id}", "Update a plan"),
    _p("activate_plan", ["POST"], "/v1/plans/{plan_id}/activate", "Activate a plan"),
    _p("archive_plan", ["POST"], "/v1/plans/{plan_id}/archive", "Archive a plan"),
    _p("clone_plan", ["POST"], "/v1/plans/{source_plan_id}/clone", "Clone a plan"),
    _p("delete_plan", ["DELETE"], "/v1/plans/{plan_id}", "Delete a draft plan"),
    # Subscription management
    _p("create_subscription", ["POST"], "/v1/subscriptions", "Subscribe a tenant"),
    _p("list_subscriptions", ["GET"], "/v1/subscriptions", "List all subscriptions"),
    _p(
        "get_subscription",
        ["GET"],
        "/v1/subscriptions/{subscription_id}",
        "View subscription",
    ),
    _p(
        "get_tenant_subscription",
        ["GET"],
        "/v1/subscriptions/tenant/{tenant_id}/active",
        "View tenant active subscription",
    ),
    _p(
        "change_plan",
        ["POST"],
        "/v1/subscriptions/change-plan",
        "Change a tenant's plan",
    ),
    _p(
        "cancel_subscription",
        ["POST"],
        "/v1/subscriptions/cancel",
        "Cancel a subscription",
    ),
    _p(
        "update_overrides",
        ["PUT"],
        "/v1/subscriptions/{subscription_id}/overrides",
        "Update subscription overrides",
    ),
    # Discount management
    _p("create_discount", ["POST"], "/v1/discounts", "Create a discount"),
    _p("list_discounts", ["GET"], "/v1/discounts", "List discounts"),
    _p("get_discount", ["GET"], "/v1/discounts/{discount_id}", "View discount"),
    _p(
        "get_discount_by_code",
        ["GET"],
        "/v1/discounts/code/{code}",
        "Lookup discount by code",
    ),
    _p("update_discount", ["PUT"], "/v1/discounts/{discount_id}", "Update a discount"),
    _p(
        "validate_discount",
        ["POST"],
        "/v1/discounts/validate",
        "Validate a discount code",
    ),
    _p(
        "disable_discount",
        ["POST"],
        "/v1/discounts/{discount_id}/disable",
        "Disable a discount",
    ),
    _p(
        "delete_discount",
        ["DELETE"],
        "/v1/discounts/{discount_id}",
        "Delete a discount",
    ),
    # Usage
    _p(
        "get_tenant_usage",
        ["GET"],
        "/v1/usage/tenant/{tenant_id}/summary",
        "View tenant usage summary",
    ),
]


# ---------------------------------------------------------------------------
# Application User — limited platform access (original boilerplate role)
# ---------------------------------------------------------------------------

USER_PERMISSIONS: list[Permission] = [
    _p("list_users", ["GET"], "/v1/users/", "List application users"),
    _p("get_user_profile", ["GET"], "/v1/users/me", "View own profile"),
    _p("delete_user", ["DELETE"], "/v1/users/account", "Delete own account"),
    _p("get_my_usage", ["GET"], "/v1/usage/my-usage", "View own usage"),
]


# ---------------------------------------------------------------------------
# Tenant Super Admin — full tenant management
# ---------------------------------------------------------------------------

SUPER_ADMIN_PERMISSIONS: list[Permission] = [
    # User management
    _p(
        "create_system_user", ["POST"], "/v1/system-users/signup", "Invite system users"
    ),
    _p("list_system_users", ["GET"], "/v1/system-users/", "List system users"),
    _p(
        "update_system_user",
        ["PATCH"],
        "/v1/system-users/{user_id}",
        "Update system user",
    ),
    _p(
        "delete_system_user",
        ["DELETE"],
        "/v1/system-users/{user_id}",
        "Delete system user",
    ),
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    # Branch management
    _p("create_branch", ["POST"], "/v1/branches", "Create branch"),
    _p("list_branches", ["GET"], "/v1/branches", "List branches"),
    _p("get_branch", ["GET"], "/v1/branches/{branch_id}", "View branch"),
    _p("update_branch", ["PUT"], "/v1/branches/{branch_id}", "Update branch"),
    _p(
        "deactivate_branch",
        ["POST"],
        "/v1/branches/{branch_id}/deactivate",
        "Deactivate branch",
    ),
    _p("delete_branch", ["DELETE"], "/v1/branches/{branch_id}", "Delete branch"),
    # Department management
    _p("create_department", ["POST"], "/v1/departments/", "Create department"),
    _p("list_departments", ["GET"], "/v1/departments/", "List departments"),
    _p("get_department", ["GET"], "/v1/departments/{department_id}", "View department"),
    _p(
        "update_department",
        ["PATCH"],
        "/v1/departments/{department_id}",
        "Update department",
    ),
    _p(
        "delete_department",
        ["DELETE"],
        "/v1/departments/{department_id}",
        "Delete department",
    ),
    # Tenant config
    _p("list_tenants", ["GET"], "/v1/tenants/", "List own tenant"),
    _p("get_tenant", ["GET"], "/v1/tenants/{tenant_id}", "View own tenant"),
    _p("update_tenant", ["PATCH"], "/v1/tenants/{tenant_id}", "Update own tenant"),
    # Dashboard
    _p("dashboard_stats", ["GET"], "/v1/dashboard/stats", "View dashboard stats"),
    _p("dashboard_visitors", ["GET"], "/v1/dashboard/visitors", "View visitor log"),
    _p("super_admin_analytics", ["GET"], "/v1/super-admin/analytics", "View analytics"),
    _p(
        "super_admin_departments",
        ["GET"],
        "/v1/super-admin/departments",
        "List departments",
    ),
    _p(
        "super_admin_create_dept",
        ["POST"],
        "/v1/super-admin/departments",
        "Create department",
    ),
    _p("super_admin_list_users", ["GET"], "/v1/super-admin/admins", "List users"),
    _p("super_admin_invite", ["POST"], "/v1/super-admin/admins/invite", "Invite user"),
    # Visitor management (super admin can also do)
    _p("check_in", ["POST"], "/v1/visitors/check-in", "Check in visitor"),
    _p("check_out", ["POST"], "/v1/visitors/check-out", "Check out visitor"),
    _p("list_active_visitors", ["GET"], "/v1/visitors/active", "List active visitors"),
    _p("list_visit_sessions", ["GET"], "/v1/visitors/sessions", "List visit sessions"),
    _p(
        "get_visit_session",
        ["GET"],
        "/v1/visitors/sessions/{session_id}",
        "View visit session",
    ),
    # Visitor profiles
    _p(
        "search_profiles",
        ["GET"],
        "/v1/visitor-profiles/search",
        "Search visitor profiles",
    ),
    _p("list_profiles", ["GET"], "/v1/visitor-profiles/", "List visitor profiles"),
    _p(
        "get_profile",
        ["GET"],
        "/v1/visitor-profiles/{profile_id}",
        "View visitor profile",
    ),
    _p(
        "update_profile",
        ["PATCH"],
        "/v1/visitor-profiles/{profile_id}",
        "Update visitor profile",
    ),
    # Appointments
    _p("create_appointment", ["POST"], "/v1/appointments/", "Create appointment"),
    _p("list_appointments", ["GET"], "/v1/appointments/", "List appointments"),
    _p(
        "get_appointment",
        ["GET"],
        "/v1/appointments/{appointment_id}",
        "View appointment",
    ),
    _p(
        "update_appointment",
        ["PATCH"],
        "/v1/appointments/{appointment_id}",
        "Update appointment",
    ),
    _p(
        "delete_appointment",
        ["DELETE"],
        "/v1/appointments/{appointment_id}",
        "Delete appointment",
    ),
    # Privacy notices
    _p(
        "create_privacy_notice",
        ["POST"],
        "/v1/privacy-notices/",
        "Create privacy notice",
    ),
    _p(
        "get_active_notice", ["GET"], "/v1/privacy-notices/active", "View active notice"
    ),
    _p("list_privacy_notices", ["GET"], "/v1/privacy-notices/", "List privacy notices"),
    _p(
        "update_privacy_notice",
        ["PATCH"],
        "/v1/privacy-notices/{notice_id}",
        "Update privacy notice",
    ),
    # Compliance & audit
    _p("list_audit_logs", ["GET"], "/v1/audit-logs/", "View audit logs"),
    _p("list_dsr", ["GET"], "/v1/dsr/", "List data subject requests"),
    _p("create_dsr", ["POST"], "/v1/dsr/", "Create data subject request"),
    _p("get_dsr", ["GET"], "/v1/dsr/{dsr_id}", "View data subject request"),
    _p("update_dsr", ["PATCH"], "/v1/dsr/{dsr_id}", "Update data subject request"),
    _p("create_incident", ["POST"], "/v1/incidents/", "Create incident"),
    _p("list_incidents", ["GET"], "/v1/incidents/", "List incidents"),
    _p("get_incident", ["GET"], "/v1/incidents/{incident_id}", "View incident"),
    # Retention
    _p(
        "create_retention_policy",
        ["POST"],
        "/v1/retention-policies/",
        "Create retention policy",
    ),
    _p(
        "list_retention_policies",
        ["GET"],
        "/v1/retention-policies/",
        "List retention policies",
    ),
    _p(
        "update_retention_policy",
        ["PATCH"],
        "/v1/retention-policies/{policy_id}",
        "Update retention policy",
    ),
    # Sub-processors
    _p("create_sub_processor", ["POST"], "/v1/sub-processors/", "Create sub-processor"),
    _p("list_sub_processors", ["GET"], "/v1/sub-processors/", "List sub-processors"),
    _p(
        "update_sub_processor",
        ["PATCH"],
        "/v1/sub-processors/{sp_id}",
        "Update sub-processor",
    ),
    _p(
        "delete_sub_processor",
        ["DELETE"],
        "/v1/sub-processors/{sp_id}",
        "Delete sub-processor",
    ),
    # Compliance register
    _p("list_dpr", ["GET"], "/v1/compliance/register", "List DPR entries"),
    _p("create_dpr", ["POST"], "/v1/compliance/register", "Add DPR entry"),
    _p(
        "list_deletion_logs",
        ["GET"],
        "/v1/compliance/deletion-logs",
        "List deletion logs",
    ),
    # Documents
    _p(
        "upload_intent",
        ["POST"],
        "/v1/documents/upload-intents",
        "Create upload intent",
    ),
    _p("complete_upload", ["POST"], "/v1/documents/complete", "Complete upload"),
    _p("get_document", ["GET"], "/v1/documents/{document_id}", "View document"),
    _p("delete_document", ["DELETE"], "/v1/documents/{document_id}", "Delete document"),
    # Branding
    _p(
        "get_branding",
        ["GET"],
        "/v1/branding/tenant/{tenant_id}",
        "View tenant branding",
    ),
    _p("set_branding", ["PUT"], "/v1/branding", "Create or update tenant branding"),
    _p(
        "reset_branding",
        ["DELETE"],
        "/v1/branding",
        "Reset tenant branding to defaults",
    ),
    # Usage
    _p("get_my_usage", ["GET"], "/v1/usage/my-usage", "View own tenant usage"),
]


# ---------------------------------------------------------------------------
# Department Admin — department-scoped operations
# ---------------------------------------------------------------------------

DEPT_ADMIN_PERMISSIONS: list[Permission] = [
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    # Branding (read-only)
    _p(
        "get_branding",
        ["GET"],
        "/v1/branding/tenant/{tenant_id}",
        "View tenant branding",
    ),
    # Department management (own department)
    _p("create_department", ["POST"], "/v1/departments/", "Create department"),
    _p("list_departments", ["GET"], "/v1/departments/", "List departments"),
    _p("get_department", ["GET"], "/v1/departments/{department_id}", "View department"),
    _p(
        "update_department",
        ["PATCH"],
        "/v1/departments/{department_id}",
        "Update department",
    ),
    # Dashboard
    _p("dashboard_stats", ["GET"], "/v1/dashboard/stats", "View dashboard stats"),
    _p("dashboard_visitors", ["GET"], "/v1/dashboard/visitors", "View visitor log"),
    # Visitor management
    _p("check_in", ["POST"], "/v1/visitors/check-in", "Check in visitor"),
    _p("check_out", ["POST"], "/v1/visitors/check-out", "Check out visitor"),
    _p("list_active_visitors", ["GET"], "/v1/visitors/active", "List active visitors"),
    _p("list_visit_sessions", ["GET"], "/v1/visitors/sessions", "List visit sessions"),
    _p(
        "get_visit_session",
        ["GET"],
        "/v1/visitors/sessions/{session_id}",
        "View visit session",
    ),
    # Visitor profiles
    _p(
        "search_profiles",
        ["GET"],
        "/v1/visitor-profiles/search",
        "Search visitor profiles",
    ),
    _p("list_profiles", ["GET"], "/v1/visitor-profiles/", "List visitor profiles"),
    _p(
        "get_profile",
        ["GET"],
        "/v1/visitor-profiles/{profile_id}",
        "View visitor profile",
    ),
    _p(
        "update_profile",
        ["PATCH"],
        "/v1/visitor-profiles/{profile_id}",
        "Update visitor profile",
    ),
    # Appointments
    _p("create_appointment", ["POST"], "/v1/appointments/", "Create appointment"),
    _p("list_appointments", ["GET"], "/v1/appointments/", "List appointments"),
    _p(
        "get_appointment",
        ["GET"],
        "/v1/appointments/{appointment_id}",
        "View appointment",
    ),
    _p(
        "update_appointment",
        ["PATCH"],
        "/v1/appointments/{appointment_id}",
        "Update appointment",
    ),
    _p(
        "delete_appointment",
        ["DELETE"],
        "/v1/appointments/{appointment_id}",
        "Delete appointment",
    ),
    # Privacy notice (read active)
    _p(
        "get_active_notice", ["GET"], "/v1/privacy-notices/active", "View active notice"
    ),
    # Documents
    _p(
        "upload_intent",
        ["POST"],
        "/v1/documents/upload-intents",
        "Create upload intent",
    ),
    _p("complete_upload", ["POST"], "/v1/documents/complete", "Complete upload"),
    _p("get_document", ["GET"], "/v1/documents/{document_id}", "View document"),
    _p("delete_document", ["DELETE"], "/v1/documents/{document_id}", "Delete document"),
    # Usage
    _p("get_my_usage", ["GET"], "/v1/usage/my-usage", "View own tenant usage"),
]


# ---------------------------------------------------------------------------
# Receptionist — check-in/out, appointments, profiles
# ---------------------------------------------------------------------------

RECEPTIONIST_PERMISSIONS: list[Permission] = [
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    # Branding (read-only — needed for badge generation UI)
    _p(
        "get_branding",
        ["GET"],
        "/v1/branding/tenant/{tenant_id}",
        "View tenant branding",
    ),
    # Visitor management
    _p("check_in", ["POST"], "/v1/visitors/check-in", "Check in visitor"),
    _p("check_out", ["POST"], "/v1/visitors/check-out", "Check out visitor"),
    _p("list_active_visitors", ["GET"], "/v1/visitors/active", "List active visitors"),
    _p(
        "get_visit_session",
        ["GET"],
        "/v1/visitors/sessions/{session_id}",
        "View visit session",
    ),
    # Visitor profiles
    _p(
        "search_profiles",
        ["GET"],
        "/v1/visitor-profiles/search",
        "Search visitor profiles",
    ),
    _p(
        "get_profile",
        ["GET"],
        "/v1/visitor-profiles/{profile_id}",
        "View visitor profile",
    ),
    _p(
        "update_profile",
        ["PATCH"],
        "/v1/visitor-profiles/{profile_id}",
        "Update visitor profile",
    ),
    # Appointments
    _p("create_appointment", ["POST"], "/v1/appointments/", "Create appointment"),
    _p("list_appointments", ["GET"], "/v1/appointments/", "List appointments"),
    _p(
        "get_appointment",
        ["GET"],
        "/v1/appointments/{appointment_id}",
        "View appointment",
    ),
    _p(
        "update_appointment",
        ["PATCH"],
        "/v1/appointments/{appointment_id}",
        "Update appointment",
    ),
    # Privacy notice (read active for display during check-in)
    _p(
        "get_active_notice", ["GET"], "/v1/privacy-notices/active", "View active notice"
    ),
    # Documents (upload visitor photos/IDs)
    _p(
        "upload_intent",
        ["POST"],
        "/v1/documents/upload-intents",
        "Create upload intent",
    ),
    _p("complete_upload", ["POST"], "/v1/documents/complete", "Complete upload"),
    _p("get_document", ["GET"], "/v1/documents/{document_id}", "View document"),
]


# ---------------------------------------------------------------------------
# Auditor — read-only audit access
# ---------------------------------------------------------------------------

AUDITOR_PERMISSIONS: list[Permission] = [
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    _p("list_audit_logs", ["GET"], "/v1/audit-logs/", "View audit logs"),
    _p("list_visit_sessions", ["GET"], "/v1/visitors/sessions", "List visit sessions"),
    _p(
        "get_visit_session",
        ["GET"],
        "/v1/visitors/sessions/{session_id}",
        "View visit session",
    ),
    _p("list_profiles", ["GET"], "/v1/visitor-profiles/", "List visitor profiles"),
    _p(
        "get_profile",
        ["GET"],
        "/v1/visitor-profiles/{profile_id}",
        "View visitor profile",
    ),
    _p("list_dpr", ["GET"], "/v1/compliance/register", "List DPR entries"),
    _p("create_dpr", ["POST"], "/v1/compliance/register", "Add DPR entry"),
    _p(
        "list_deletion_logs",
        ["GET"],
        "/v1/compliance/deletion-logs",
        "List deletion logs",
    ),
]


# ---------------------------------------------------------------------------
# Security Officer — incident management
# ---------------------------------------------------------------------------

SECURITY_OFFICER_PERMISSIONS: list[Permission] = [
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    _p("create_incident", ["POST"], "/v1/incidents/", "Create incident"),
    _p("list_incidents", ["GET"], "/v1/incidents/", "List incidents"),
    _p("get_incident", ["GET"], "/v1/incidents/{incident_id}", "View incident"),
]


# ---------------------------------------------------------------------------
# DPO (Data Protection Officer) — privacy & compliance
# ---------------------------------------------------------------------------

DPO_PERMISSIONS: list[Permission] = [
    _p("get_my_profile", ["GET"], "/v1/system-users/me", "View own profile"),
    # Data subject requests
    _p("create_dsr", ["POST"], "/v1/dsr/", "Create data subject request"),
    _p("list_dsr", ["GET"], "/v1/dsr/", "List data subject requests"),
    _p("get_dsr", ["GET"], "/v1/dsr/{dsr_id}", "View data subject request"),
    _p("update_dsr", ["PATCH"], "/v1/dsr/{dsr_id}", "Update data subject request"),
    # Privacy notices
    _p(
        "create_privacy_notice",
        ["POST"],
        "/v1/privacy-notices/",
        "Create privacy notice",
    ),
    _p(
        "get_active_notice", ["GET"], "/v1/privacy-notices/active", "View active notice"
    ),
    _p("list_privacy_notices", ["GET"], "/v1/privacy-notices/", "List privacy notices"),
    _p(
        "update_privacy_notice",
        ["PATCH"],
        "/v1/privacy-notices/{notice_id}",
        "Update privacy notice",
    ),
    # Retention policies
    _p(
        "create_retention_policy",
        ["POST"],
        "/v1/retention-policies/",
        "Create retention policy",
    ),
    _p(
        "list_retention_policies",
        ["GET"],
        "/v1/retention-policies/",
        "List retention policies",
    ),
    _p(
        "update_retention_policy",
        ["PATCH"],
        "/v1/retention-policies/{policy_id}",
        "Update retention policy",
    ),
    # Sub-processors
    _p("create_sub_processor", ["POST"], "/v1/sub-processors/", "Create sub-processor"),
    _p("list_sub_processors", ["GET"], "/v1/sub-processors/", "List sub-processors"),
    _p(
        "update_sub_processor",
        ["PATCH"],
        "/v1/sub-processors/{sp_id}",
        "Update sub-processor",
    ),
    _p(
        "delete_sub_processor",
        ["DELETE"],
        "/v1/sub-processors/{sp_id}",
        "Delete sub-processor",
    ),
    # Compliance
    _p("list_dpr", ["GET"], "/v1/compliance/register", "List DPR entries"),
    _p("create_dpr", ["POST"], "/v1/compliance/register", "Add DPR entry"),
    _p(
        "list_deletion_logs",
        ["GET"],
        "/v1/compliance/deletion-logs",
        "List deletion logs",
    ),
    # Audit logs (DPO can read)
    _p("list_audit_logs", ["GET"], "/v1/audit-logs/", "View audit logs"),
]


# ---------------------------------------------------------------------------
# Master mapping: role name → permission list
# ---------------------------------------------------------------------------

DEFAULT_ROLE_PERMISSIONS: dict[str, list[Permission]] = {
    "admin": ADMIN_PERMISSIONS,
    "user": USER_PERMISSIONS,
    "super_admin": SUPER_ADMIN_PERMISSIONS,
    "dept_admin": DEPT_ADMIN_PERMISSIONS,
    "receptionist": RECEPTIONIST_PERMISSIONS,
    "auditor": AUDITOR_PERMISSIONS,
    "security_officer": SECURITY_OFFICER_PERMISSIONS,
    "dpo": DPO_PERMISSIONS,
}


def get_default_permissions_for_role(role: str) -> PermissionList:
    """Return the default PermissionList for a given role.

    Raises KeyError if the role is unknown.
    """
    perms = DEFAULT_ROLE_PERMISSIONS.get(role)
    if perms is None:
        raise KeyError(f"No default permissions defined for role: {role}")
    return PermissionList(permissions=perms)
