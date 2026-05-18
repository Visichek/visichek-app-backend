from __future__ import annotations

from core.email.types import MountedTemplate
from email_templates import admin_invite as _admin_invite
from email_templates import admin_otp_code as _admin_otp_code
from email_templates import notification_templates as _notification_templates
from email_templates import notification_test as _notification_test
from email_templates import onboarding_accepted as _onboarding_accepted
from email_templates import onboarding_partial_accepted as _onboarding_partial_accepted
from email_templates import password_reset as _password_reset
from email_templates import password_reset_temp as _password_reset_temp
from email_templates import starter_template as _starter
from email_templates import visitor_badge_approved as _visitor_badge_approved
from email_templates import support_case_acknowledged_tenant as _sc_ack_tenant
from email_templates import (
    support_case_admin_replied_tenant as _sc_admin_replied_tenant,
)
from email_templates import support_case_assigned_admin as _sc_assigned_admin
from email_templates import support_case_awaiting_tenant as _sc_awaiting_tenant
from email_templates import support_case_closed_tenant as _sc_closed_tenant
from email_templates import support_case_opened_admin as _sc_opened_admin
from email_templates import support_case_opened_tenant as _sc_opened_tenant
from email_templates import support_case_resolved_tenant as _sc_resolved_tenant
from email_templates import support_case_sla_breach_admin as _sc_sla_breach_admin
from email_templates import (
    support_case_tenant_replied_admin as _sc_tenant_replied_admin,
)


def _mount(module) -> MountedTemplate:
    return MountedTemplate(
        key=module.TEMPLATE_KEY,
        subject=module.SUBJECT,
        render_html=module.render_html,
        render_text=module.render_text,
    )


def get_mounted_templates() -> list[MountedTemplate]:
    return [
        _mount(_starter),
        # Diagnostics (Issue 6 — POST /v1/notifications/test)
        _mount(_notification_test),
        # Application-admin lifecycle: invite welcome + per-login OTP.
        _mount(_admin_invite),
        _mount(_admin_otp_code),
        # Self-service password reset (both admin + system_user — token-based
        # forgot-password flow).
        _mount(_password_reset),
        # Authority-driven reset notification: an admin / super_admin
        # triggered the reset; the user receives a temporary password
        # they must change on next sign-in.
        _mount(_password_reset_temp),
        # Tenant self-onboarding: accept + partial-accept welcome emails
        # carry the generated temporary password the new super_admin
        # needs to sign in for the first time.
        _mount(_onboarding_accepted),
        _mount(_onboarding_partial_accepted),
        # Visitor lifecycle (Issue 7 — badge email on approval)
        _mount(_visitor_badge_approved),
        # Per-event notification fan-out (Issue 6 / Phase B2 —
        # incident deadline, visitor check-in, appointment reminder,
        # DSR submitted, subscription alert, new user added).
        *(_mount(t) for t in _notification_templates.ALL_TEMPLATES),
        # Support-case (tenant-facing)
        _mount(_sc_opened_tenant),
        _mount(_sc_ack_tenant),
        _mount(_sc_admin_replied_tenant),
        _mount(_sc_awaiting_tenant),
        _mount(_sc_resolved_tenant),
        _mount(_sc_closed_tenant),
        # Support-case (admin-facing)
        _mount(_sc_opened_admin),
        _mount(_sc_assigned_admin),
        _mount(_sc_tenant_replied_admin),
        _mount(_sc_sla_breach_admin),
    ]
