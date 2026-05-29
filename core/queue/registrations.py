"""Side-effect module that imports every writer / precompute registrar.

Add a new line here whenever a module defines ``@write_handler`` or
``@register_precompute`` decorators that need to be visible to both the
web process (for precompute fanout) and the celery workers (for
``db.write`` dispatch and ``run_precompute`` execution).

Kept as a single aggregator so any forgetful ``import`` in one process
(and not the other) surfaces immediately instead of only misbehaving
under specific task routes.
"""

from __future__ import annotations

# Department — reference implementation
from services import department_writer as _department_writer  # noqa: F401

# Cluster 1 — low-blast-radius tenant CRUD
from services import sub_processor_writer as _sub_processor_writer  # noqa: F401
from services import retention_policy_writer as _retention_policy_writer  # noqa: F401
from services import privacy_notice_writer as _privacy_notice_writer  # noqa: F401
from services import dsr_writer as _dsr_writer  # noqa: F401
from services import branch_writer as _branch_writer  # noqa: F401
from services import branding_writer as _branding_writer  # noqa: F401

# Cluster 2 — core tenant CRUD
from services import appointment_writer as _appointment_writer  # noqa: F401
from services import host_writer as _host_writer  # noqa: F401
from services import incident_writer as _incident_writer  # noqa: F401
from services import visitor_profile_writer as _visitor_profile_writer  # noqa: F401
from services import checkin_config_writer as _checkin_config_writer  # noqa: F401
from services import tenant_settings_writer as _tenant_settings_writer  # noqa: F401

# Cluster 3 — tenant + application-admin overlap (user-mgmt calls invalidate_gate)
from services import tenant_writer as _tenant_writer  # noqa: F401
from services import system_user_writer as _system_user_writer  # noqa: F401

# Cluster 6 — precompute-only dashboards / read-model aggregations
from services import read_precompute as _read_precompute  # noqa: F401

# Cluster 5 — per-user notifications / settings
from services import notification_writer as _notification_writer  # noqa: F401
from services import user_settings_writer as _user_settings_writer  # noqa: F401
from services import user_location_writer as _user_location_writer  # noqa: F401

# Push notifications — registers the @task("push.send") handler so both
# web (enqueue) and worker (dispatch) processes know the task at boot.
from services import push_service as _push_service  # noqa: F401

# Cluster 4 — billing / plans / discounts
from services import discount_writer as _discount_writer  # noqa: F401

# Discount-available fan-out tasks (discount.announce / .announce_batch).
# Imported here so both web + worker register the @task handlers at boot.
from services import (  # noqa: F401
    discount_notification_service as _discount_notification_service,
)
from services import invoice_writer as _invoice_writer  # noqa: F401
from services import plan_writer as _plan_writer  # noqa: F401
from services import subscription_writer as _subscription_writer  # noqa: F401
from services import compliance_writer as _compliance_writer  # noqa: F401

# Marketing pricing-page overlay — overlay PATCH/DELETE + the
# GLOBAL-scope precompute loader that the public landing page reads.
from services import pricing_marketing_writer as _pricing_marketing_writer  # noqa: F401

# FAQ overlay — mirrors the pricing-marketing pattern (singleton
# overlay + global precompute) for the public FAQ page.
from services import faq_writer as _faq_writer  # noqa: F401

# Cluster 7 — platform support cases (tenant ↔ app-admin threads)
# Registering the service module is enough to pull in the precompute
# decorators; the writer module below registers the write handlers.
from services import support_case_service as _support_case_service  # noqa: F401
from services import support_case_writer as _support_case_writer  # noqa: F401

# Cluster 9 — onboarding submissions (bulk only; single-item flows stay sync)
from services import onboarding_writer as _onboarding_writer  # noqa: F401

# Cluster 8 — visitor check-in v2: tenant enums + KYC feed-throughs
from services import tenant_enum_writer as _tenant_enum_writer  # noqa: F401

# Cluster 10 — visitor bulk-action writers
from services import visitor_bulk_writer as _visitor_bulk_writer  # noqa: F401
from services import checkin_bulk_writer as _checkin_bulk_writer  # noqa: F401

# Cluster 11 — tenant form builder (precompute-only; mutations stay sync
# because the autosave / publish UX needs the result in the response).
from services import tenant_form_writer as _tenant_form_writer  # noqa: F401

# Cluster 12 — blog backend port (writers + precomputes live under
# blog/writers/). Imports the modules for their decorator side-effects.
from blog.writers import blog_writer as _blog_writer  # noqa: F401
from blog.writers import media_writer as _media_writer  # noqa: F401

# Cluster 13 — legal documents (writers + precomputes live under
# legal/writers/). Platform-level legal copy (privacy policy, ToS, etc.).
from legal.writers import legal_document_writer as _legal_document_writer  # noqa: F401
