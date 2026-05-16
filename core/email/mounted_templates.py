from __future__ import annotations

from core.email.types import MountedTemplate
from email_templates import notification_test as _notification_test
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
        # Visitor lifecycle (Issue 7 — badge email on approval)
        _mount(_visitor_badge_approved),
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
