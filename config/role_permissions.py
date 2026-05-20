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
    # Admin search + lifecycle
    _p("search_admins", ["GET"], "/v1/admins/search", "Search application admins"),
    _p(
        "update_admin_access_preset",
        ["PATCH"],
        "/v1/admins/{admin_id}/access-preset",
        "Update an admin's access preset",
    ),
    _p(
        "reset_system_user_password",
        ["POST"],
        "/v1/admins/system-users/{user_id}/reset-password",
        "Force-reset a tenant system user password",
    ),
    # Tenant lifecycle (admin)
    _p("admin_create_tenant", ["POST"], "/v1/tenants", "Create a tenant (admin)"),
    _p("admin_list_tenants", ["GET"], "/v1/tenants", "List tenants (admin)"),
    _p(
        "promote_super_admin",
        ["POST"],
        "/v1/admins/tenants/{tenant_id}/super-admins",
        "Promote an existing user to tenant super admin",
    ),
    _p(
        "replace_super_admin",
        ["POST"],
        "/v1/admins/tenants/{tenant_id}/super-admins/replace",
        "Replace tenant's lone super admin with a new one",
    ),
    _p(
        "offboard_tenant",
        ["POST"],
        "/v1/admins/tenants/{tenant_id}/offboard",
        "Offboard a tenant",
    ),
    _p(
        "offboarding_summary",
        ["GET"],
        "/v1/admins/tenants/{tenant_id}/offboarding-summary",
        "View tenant offboarding summary",
    ),
    _p(
        "bulk_offboard_tenants",
        ["POST"],
        "/v1/tenants/bulk/offboard",
        "Bulk offboard tenants",
    ),
    # Tenant self-onboarding queue
    _p(
        "list_onboarding_submissions",
        ["GET"],
        "/v1/tenants/onboarding",
        "List onboarding submissions",
    ),
    _p(
        "list_marketing_opt_ins",
        ["GET"],
        "/v1/tenants/onboarding/marketing-opt-ins",
        "List marketing opt-in emails",
    ),
    _p(
        "get_onboarding_submission",
        ["GET"],
        "/v1/tenants/onboarding/{submission_id}",
        "View onboarding submission",
    ),
    _p(
        "accept_onboarding_submission",
        ["POST"],
        "/v1/tenants/onboarding/{submission_id}/accept",
        "Accept onboarding submission",
    ),
    _p(
        "partial_accept_onboarding_submission",
        ["POST"],
        "/v1/tenants/onboarding/{submission_id}/partial-accept",
        "Partially accept onboarding submission",
    ),
    _p(
        "reject_onboarding_submission",
        ["POST"],
        "/v1/tenants/onboarding/{submission_id}/reject",
        "Reject onboarding submission",
    ),
    _p(
        "archive_onboarding_submission",
        ["POST"],
        "/v1/tenants/onboarding/{submission_id}/archive",
        "Archive onboarding submission",
    ),
    _p(
        "bulk_archive_onboarding",
        ["POST"],
        "/v1/tenants/onboarding/bulk/archive",
        "Bulk archive onboarding submissions",
    ),
    _p(
        "bulk_reject_onboarding",
        ["POST"],
        "/v1/tenants/onboarding/bulk/reject",
        "Bulk reject onboarding submissions",
    ),
    # Admin dashboard (beyond /stats)
    _p(
        "dashboard_attention",
        ["GET"],
        "/v1/admins/dashboard/attention",
        "View admin attention queue",
    ),
    _p(
        "dashboard_email_outbox",
        ["GET"],
        "/v1/admins/dashboard/email-outbox",
        "View admin email outbox",
    ),
    _p(
        "dashboard_billing",
        ["GET"],
        "/v1/admins/dashboard/billing",
        "View admin billing dashboard",
    ),
    _p(
        "dashboard_billing_discrepancies",
        ["GET"],
        "/v1/admins/dashboard/billing/discrepancies",
        "View billing discrepancies",
    ),
    # Application admin support cases
    _p(
        "admin_list_support_cases",
        ["GET"],
        "/v1/admins/support-cases",
        "List all support cases (admin)",
    ),
    _p(
        "admin_support_cases_sla",
        ["GET"],
        "/v1/admins/support-cases/approaching-sla",
        "List SLA-at-risk support cases",
    ),
    _p(
        "admin_get_support_case",
        ["GET"],
        "/v1/admins/support-cases/{case_id}",
        "Retrieve a support case (admin)",
    ),
    _p(
        "admin_list_support_case_messages",
        ["GET"],
        "/v1/admins/support-cases/{case_id}/messages",
        "List messages on a support case (admin)",
    ),
    _p(
        "admin_reply_support_case",
        ["POST"],
        "/v1/admins/support-cases/{case_id}/messages",
        "Reply on a support case (admin)",
    ),
    _p(
        "admin_assign_support_case",
        ["POST"],
        "/v1/admins/support-cases/{case_id}/assign",
        "Assign a support case (admin)",
    ),
    _p(
        "admin_transition_support_case",
        ["POST"],
        "/v1/admins/support-cases/{case_id}/transition",
        "Transition a support case (admin)",
    ),
    _p(
        "admin_support_case_attachment_intent",
        ["POST"],
        "/v1/admins/support-cases/{case_id}/attachments/intent",
        "Create support case attachment intent (admin)",
    ),
    _p(
        "admin_register_support_case_attachment",
        ["POST"],
        "/v1/admins/support-cases/{case_id}/attachments",
        "Register support case attachment (admin)",
    ),
    _p(
        "admin_support_case_bulk_assign",
        ["POST"],
        "/v1/admins/support-cases/bulk/assign",
        "Bulk assign support cases",
    ),
    _p(
        "admin_support_case_bulk_status",
        ["POST"],
        "/v1/admins/support-cases/bulk/status",
        "Bulk transition support cases",
    ),
    _p(
        "admin_support_case_bulk_close",
        ["POST"],
        "/v1/admins/support-cases/bulk/close",
        "Bulk close support cases",
    ),
    # Audit logs (admin)
    _p(
        "admin_list_audit_logs",
        ["GET"],
        "/v1/audit-logs/admin",
        "List platform audit log",
    ),
    _p(
        "admin_export_audit_logs",
        ["GET"],
        "/v1/audit-logs/admin/export",
        "Export platform audit log",
    ),
    # Invoices (admin)
    _p(
        "admin_list_invoices",
        ["GET"],
        "/v1/invoices/admin",
        "List all invoices (admin)",
    ),
    _p(
        "admin_invoice_bulk_download",
        ["POST"],
        "/v1/invoices/bulk/download",
        "Bulk-download invoices",
    ),
    _p(
        "admin_invoice_bulk_void",
        ["POST"],
        "/v1/invoices/bulk/void",
        "Bulk-void invoices",
    ),
    # Payment webhooks (admin)
    _p(
        "admin_list_webhook_events",
        ["GET"],
        "/v1/payments/webhooks/events",
        "List webhook events",
    ),
    _p(
        "admin_replay_webhook_event",
        ["POST"],
        "/v1/payments/webhooks/replay/{event_id}",
        "Replay a webhook event",
    ),
    # Plans (bulk + feature toggles)
    _p(
        "bulk_activate_plans",
        ["POST"],
        "/v1/plans/bulk/activate",
        "Bulk activate plans",
    ),
    _p(
        "bulk_archive_plans",
        ["POST"],
        "/v1/plans/bulk/archive",
        "Bulk archive plans",
    ),
    _p(
        "bulk_delete_plans",
        ["POST"],
        "/v1/plans/bulk/delete",
        "Bulk delete plans",
    ),
    _p(
        "set_plan_feature",
        ["POST"],
        "/v1/plans/{plan_id}/features/{feature_key}",
        "Toggle plan feature",
    ),
    # Subscriptions (bulk)
    _p(
        "bulk_cancel_subscriptions",
        ["POST"],
        "/v1/subscriptions/bulk/cancel",
        "Bulk cancel subscriptions",
    ),
    # Discounts (bulk)
    _p(
        "bulk_disable_discounts",
        ["POST"],
        "/v1/discounts/bulk/disable",
        "Bulk disable discounts",
    ),
    _p(
        "bulk_delete_discounts",
        ["POST"],
        "/v1/discounts/bulk/delete",
        "Bulk delete discounts",
    ),
    # Platform settings (singleton + 2FA setup)
    _p(
        "get_platform_settings_admin",
        ["GET"],
        "/v1/admins/platform-settings",
        "View platform settings (admin)",
    ),
    _p(
        "request_maintenance_otp_admin",
        ["POST"],
        "/v1/admins/platform-settings/maintenance/request-otp",
        "Request OTP to change maintenance mode (admin)",
    ),
    _p(
        "update_platform_settings_admin",
        ["PATCH"],
        "/v1/admins/platform-settings",
        "Update maintenance mode (admin)",
    ),
    _p(
        "get_platform_settings_unified",
        ["GET"],
        "/v1/platform-settings",
        "View platform settings",
    ),
    _p(
        "request_maintenance_otp_unified",
        ["POST"],
        "/v1/platform-settings/maintenance/request-otp",
        "Request OTP to change maintenance mode",
    ),
    _p(
        "update_platform_settings_unified",
        ["PATCH"],
        "/v1/platform-settings",
        "Update maintenance mode",
    ),
    _p(
        "admin_setup_2fa",
        ["POST"],
        "/v1/admins/2fa/setup",
        "Initiate admin 2FA setup",
    ),
    # Pricing-marketing page (public GET is unauthenticated; only the
    # editorial PATCH + row DELETE require an admin token + permission).
    _p(
        "patch_pricing_marketing",
        ["PATCH"],
        "/v1/pricing-marketing",
        "Edit the public pricing-page marketing overlay",
    ),
    _p(
        "delete_pricing_marketing_row",
        ["DELETE"],
        "/v1/pricing-marketing/{kind}/{key}",
        "Remove one pricing-page overlay row (plan / feature / category)",
    ),
    # FAQ page (public GET unauthenticated; admin PATCH + row DELETE).
    _p(
        "patch_faqs",
        ["PATCH"],
        "/v1/faqs",
        "Edit the public FAQ overlay (hero copy, items, categories)",
    ),
    _p(
        "delete_faq_row",
        ["DELETE"],
        "/v1/faqs/{kind}/{key}",
        "Remove one FAQ overlay row (item / category)",
    ),
    # Blog (admin)
    _p("list_blogs", ["GET"], "/v1/blogs", "List blogs"),
    _p("list_recent_blogs", ["GET"], "/v1/blogs/recent", "List most-recent blogs"),
    _p("get_blog", ["GET"], "/v1/blogs/{blog_id}", "Get blog by id"),
    _p("create_blog", ["POST"], "/v1/blogs", "Create blog"),
    _p("update_blog", ["PATCH"], "/v1/blogs/{blog_id}", "Update blog"),
    _p("delete_blog", ["DELETE"], "/v1/blogs/{blog_id}", "Delete blog"),
    # Media (admin)
    _p("list_media", ["GET"], "/v1/media", "List media"),
    _p("list_recent_media", ["GET"], "/v1/media/recent", "List most-recent media"),
    _p(
        "list_media_by_type",
        ["GET"],
        "/v1/media/by-type/{media_type}",
        "List media filtered by type",
    ),
    _p(
        "list_media_by_category",
        ["GET"],
        "/v1/media/by-category/{category}",
        "List media filtered by category",
    ),
    _p("get_media", ["GET"], "/v1/media/{media_id}", "Get media by id"),
    _p("upload_media", ["POST"], "/v1/media/upload-media", "Upload media"),
    _p("create_media", ["POST"], "/v1/media", "Create media row with upload"),
    _p("upload_image", ["POST"], "/v1/media/upload-image", "Upload image"),
    _p("upload_video", ["POST"], "/v1/media/upload-video", "Upload video"),
    _p(
        "append_media_to_blog",
        ["POST"],
        "/v1/media/{blog_id}",
        "Append media block to blog",
    ),
    _p(
        "update_media_category",
        ["PATCH"],
        "/v1/media/{media_id}",
        "Update media category",
    ),
    _p("delete_media", ["DELETE"], "/v1/media/{media_id}", "Delete media"),
    # Blog-admin compatibility aliases (live under /v1/admins/*)
    _p(
        "admin_me_alias",
        ["GET"],
        "/v1/admins/me",
        "Current admin profile (blog-admin alias)",
    ),
    _p(
        "admin_invite_alias",
        ["POST"],
        "/v1/admins/invite",
        "Invite a new admin (blog-admin alias)",
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
    # Host management
    _p("create_host", ["POST"], "/v1/hosts", "Create host"),
    _p("list_hosts", ["GET"], "/v1/hosts", "List hosts"),
    _p("get_host", ["GET"], "/v1/hosts/{host_id}", "View host"),
    _p("update_host", ["PATCH"], "/v1/hosts/{host_id}", "Update host"),
    _p("delete_host", ["DELETE"], "/v1/hosts/{host_id}", "Delete host"),
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
    # Tenant form configuration (Issue 3 backend) — super_admin can
    # mutate visitor / appointment form templates. Mirrors the
    # frontend TENANT_FORM_CONFIGURE capability.
    _p(
        "create_tenant_form",
        ["POST"],
        "/v1/tenant-forms",
        "Create a tenant form",
    ),
    _p(
        "draft_tenant_form",
        ["POST"],
        "/v1/tenant-forms/draft/{target_type}",
        "Save tenant form draft",
    ),
    _p(
        "update_tenant_form",
        ["PATCH"],
        "/v1/tenant-forms/{form_id}",
        "Update tenant form",
    ),
    _p(
        "publish_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/publish",
        "Publish tenant form",
    ),
    _p(
        "discard_tenant_form_draft",
        ["POST"],
        "/v1/tenant-forms/{form_id}/discard-draft",
        "Discard tenant form draft",
    ),
    _p(
        "archive_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/archive",
        "Archive tenant form",
    ),
    _p(
        "clone_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/clone",
        "Clone tenant form",
    ),
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
    # Force-approve a stuck check-in (Phase A2 / Issue 17 sweep).
    # Mirrors the frontend ``CHECKIN_FORCE_APPROVE`` capability.
    # Granted ONLY to super_admin — this is the operational safety
    # valve when a KYC widget crashes or its webhook never lands.
    _p(
        "force_approve_checkin",
        ["POST"],
        "/v1/checkins/{checkin_id}/force-approve-pending",
        "Force-approve a stuck pending_verification check-in",
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
    # Host management
    _p("create_host", ["POST"], "/v1/hosts", "Create host"),
    _p("list_hosts", ["GET"], "/v1/hosts", "List hosts"),
    _p("get_host", ["GET"], "/v1/hosts/{host_id}", "View host"),
    _p("update_host", ["PATCH"], "/v1/hosts/{host_id}", "Update host"),
    # Dashboard
    _p("dashboard_stats", ["GET"], "/v1/dashboard/stats", "View dashboard stats"),
    _p("dashboard_visitors", ["GET"], "/v1/dashboard/visitors", "View visitor log"),
    # Tenant form configuration (Issue 3 backend) — dept_admin can
    # also mutate visitor / appointment form templates so they can
    # tailor the form to their department's needs.
    _p(
        "create_tenant_form",
        ["POST"],
        "/v1/tenant-forms",
        "Create a tenant form",
    ),
    _p(
        "draft_tenant_form",
        ["POST"],
        "/v1/tenant-forms/draft/{target_type}",
        "Save tenant form draft",
    ),
    _p(
        "update_tenant_form",
        ["PATCH"],
        "/v1/tenant-forms/{form_id}",
        "Update tenant form",
    ),
    _p(
        "publish_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/publish",
        "Publish tenant form",
    ),
    _p(
        "discard_tenant_form_draft",
        ["POST"],
        "/v1/tenant-forms/{form_id}/discard-draft",
        "Discard tenant form draft",
    ),
    _p(
        "archive_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/archive",
        "Archive tenant form",
    ),
    _p(
        "clone_tenant_form",
        ["POST"],
        "/v1/tenant-forms/{form_id}/clone",
        "Clone tenant form",
    ),
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
    # Hosts (read-only — needed for the appointment host picker)
    _p("list_hosts", ["GET"], "/v1/hosts", "List hosts"),
    _p("get_host", ["GET"], "/v1/hosts/{host_id}", "View host"),
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


# ---------------------------------------------------------------------------
# Application-admin access presets (Issue 10 backend)
# ---------------------------------------------------------------------------
#
# Platform admins are no longer all-or-nothing. The frontend exposes
# five presets (``content_only``, ``support_only``, ``content_support``,
# ``billing_only``, ``all_controls``) on the invite flow and via the
# ``AdminProfile.accessPreset`` field. This file owns the
# preset → permission-slice mapping so the backend enforcement matches
# the frontend nav filter exactly — the route-level dependency uses
# ``get_default_permissions_for_admin_preset`` instead of the blanket
# ``ADMIN_PERMISSIONS`` so a content-only admin literally cannot call
# tenant / subscription / discount routes.
#
# Each preset is computed as a subset of ``ADMIN_PERMISSIONS`` so we
# don't accidentally grant a content-only admin a permission that
# wasn't in the original platform-admin list.


# Helper — match permission keys by path prefix.
def _admin_perms_by_path_prefix(prefixes: tuple[str, ...]) -> list[Permission]:
    return [
        p
        for p in ADMIN_PERMISSIONS
        if any(p.path.startswith(pref) for pref in prefixes)
    ]


# Account / profile / health permissions every admin keeps — they
# need to log in, see their own profile, and check dashboard health
# regardless of scope. ``/v1/admins/me`` is the blog-admin frontend
# alias of ``/v1/admins/profile`` (see ``blog/routes/admin_compat_route``);
# kept here so the blog-admin UI works for every preset, not just
# all_controls.
_ADMIN_BASE_KEEP = _admin_perms_by_path_prefix(
    (
        "/v1/admins/profile",
        "/v1/admins/account",
        "/v1/admins/dashboard/stats",
        "/v1/admins/me",
    )
)


# ``content_only`` — blog, media, pricing-content editorial.
# Backed by ADMIN_PERMISSIONS plus the content/pricing routes.
#
# Plans: READ ONLY — content admins see plans so they can write
# marketing copy about them, but mutations stay with billing admins.
# Pricing marketing overlay: FULL EDIT — the whole point of the
# content slice is editing the public pricing page.
# Blogs + media: FULL EDIT — same rationale; the editorial team owns
# the public blog and the media library used in blog posts and
# pricing pages. Added as an explicit prefix below (NOT under the
# GET-stripped block) so POST/PATCH/DELETE actually flow through.
_ADMIN_CONTENT_PLAN_READS: list[Permission] = [
    p for p in _admin_perms_by_path_prefix(("/v1/plans",)) if "GET" in p.methods
]
ADMIN_CONTENT_PERMISSIONS: list[Permission] = (
    list(_ADMIN_BASE_KEEP)
    + _ADMIN_CONTENT_PLAN_READS
    + _admin_perms_by_path_prefix(
        (
            "/v1/pricing-marketing",
            "/v1/faqs",
            "/v1/blogs",
            "/v1/media",
        )
    )
)


# ``support_only`` — triage tenant support cases + view recent
# activity. Pulls in every ``/v1/admins/support-cases`` permission
# (read, reply, assign, transition, attachments, bulk) plus the
# operational dashboard views support teams rely on (attention queue,
# email outbox). Onboarding triage is included so support can accept
# or reject incoming tenant signups during off-hours coverage.
ADMIN_SUPPORT_PERMISSIONS: list[Permission] = list(
    _ADMIN_BASE_KEEP
) + _admin_perms_by_path_prefix(
    (
        "/v1/admins/support-cases",
        "/v1/admins/dashboard/attention",
        "/v1/admins/dashboard/email-outbox",
        "/v1/tenants/onboarding",
    )
)


# ``content_support`` — both editorial + support workflows.
ADMIN_CONTENT_SUPPORT_PERMISSIONS: list[Permission] = list(
    {
        p.key: p for p in (*ADMIN_CONTENT_PERMISSIONS, *ADMIN_SUPPORT_PERMISSIONS)
    }.values()
)


# ``billing_only`` — plans, subscriptions, discounts, usage, invoices,
# payment webhooks, and the billing dashboard. The full write set on
# those resources, but no tenants/content/support.
ADMIN_BILLING_PERMISSIONS: list[Permission] = list(
    _ADMIN_BASE_KEEP
) + _admin_perms_by_path_prefix(
    (
        "/v1/plans",
        "/v1/subscriptions",
        "/v1/discounts",
        "/v1/usage",
        "/v1/invoices",
        "/v1/payments/webhooks",
        "/v1/admins/dashboard/billing",
    )
)


# ``all_controls`` — the legacy "platform admin can do everything"
# behavior. Kept as the default for backwards compatibility with
# admins provisioned before presets shipped.
ADMIN_ALL_CONTROLS_PERMISSIONS: list[Permission] = ADMIN_PERMISSIONS


ADMIN_ACCESS_PRESETS: dict[str, list[Permission]] = {
    "content_only": ADMIN_CONTENT_PERMISSIONS,
    "support_only": ADMIN_SUPPORT_PERMISSIONS,
    "content_support": ADMIN_CONTENT_SUPPORT_PERMISSIONS,
    "billing_only": ADMIN_BILLING_PERMISSIONS,
    "all_controls": ADMIN_ALL_CONTROLS_PERMISSIONS,
}


def get_default_permissions_for_admin_preset(preset: str | None) -> PermissionList:
    """Return the permission slice an application admin gets for a preset.

    ``preset=None`` (and any unknown value) falls back to the legacy
    ``all_controls`` permission set so existing admins continue to
    work after the upgrade. New invites should supply an explicit
    preset.
    """
    key = (preset or "all_controls").lower()
    perms = ADMIN_ACCESS_PRESETS.get(key, ADMIN_ALL_CONTROLS_PERMISSIONS)
    return PermissionList(permissions=perms)
