"""Per-tenant Data Processing Agreement service.

Each tenant has its own DPA copy built from the committed template with the
Organization party block filled in from the tenant's details. Behaviour:

* Before acceptance, the copy is (re)built from the tenant's CURRENT fields on
  every read, so edits made on the onboarding "Confirm company details" screen
  (e.g. the organization address) are reflected before the tenant accepts.
* On acceptance, the resolved body is frozen — later reads return that exact
  snapshot as the immutable record of what was agreed.

All build/seed helpers degrade gracefully when the template asset is absent
(the extraction script has not been run yet): they return ``None`` rather than
raising, so tenant provisioning and reads never break.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from bson import ObjectId

from repositories.dpa_repo import get_dpa_for_tenant, update_dpa, upsert_dpa
from repositories.tenant_repo import get_tenant
from schemas.dpa_schema import (
    DpaAgreementCreate,
    DpaAgreementOut,
    DpaAgreementUpdate,
)
from services.dpa_defaults import (
    build_tenant_dpa_content,
    set_runtime_template,
    template_is_available,
)

logger = logging.getLogger(__name__)

# Source of the canonical DPA text in the database.
_DPA_SLUG = "data-processing-agreement"
_DPA_DOC_TYPE = "data_processing_agreement"


async def _load_template_from_db() -> bool:
    """Load the DPA template from the legal_documents collection into the
    in-process cache. Returns True on success. Prefers the live published_body,
    falls back to the working body. Never raises."""
    try:
        from legal.repositories.legal_document_repo import (
            get_legal_document,
            get_legal_document_by_slug,
        )

        doc = await get_legal_document_by_slug(_DPA_SLUG)
        if doc is None:
            doc = await get_legal_document({"doc_type": _DPA_DOC_TYPE})
        if doc is None:
            return False
        body = getattr(doc, "published_body", None) or getattr(doc, "body", None) or []
        if not body:
            return False
        set_runtime_template(
            title=getattr(doc, "title", None),
            summary=getattr(doc, "summary", None),
            body=body,
        )
        return True
    except Exception:
        logger.warning("DPA template load from legal_documents failed", exc_info=True)
        return False


async def ensure_dpa_template_loaded() -> bool:
    """Make sure a DPA template is available in this process, loading it from
    the database on first use. Works in web and worker processes regardless of
    which one ran the startup bootstrap. Cached after the first successful load."""
    if template_is_available():
        return True
    return await _load_template_from_db()


def _current_dpa_version() -> str:
    """Read the in-force DPA version lazily to avoid a circular import with
    services.tenant_service."""
    try:
        from services.tenant_service import CURRENT_DPA_VERSION

        return CURRENT_DPA_VERSION
    except Exception:  # pragma: no cover - defensive
        return "1.0"


async def _resolve_main_super_admin_email(tenant_id: str) -> Optional[str]:
    """Best-effort lookup of the tenant's main super_admin email. Never raises."""
    if not ObjectId.is_valid(tenant_id):
        return None
    try:
        from repositories.system_user_repo import get_main_super_admin

        main_sa = await get_main_super_admin(tenant_id)
        return getattr(main_sa, "email", None)
    except Exception:
        return None


async def _build_content_for_tenant(tenant_id: str) -> Optional[dict]:
    """Resolve the tenant's org details and build the substituted DPA content,
    or None when the template is unavailable."""
    if not await ensure_dpa_template_loaded():
        return None
    tenant = None
    if ObjectId.is_valid(tenant_id):
        tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    company_name = getattr(tenant, "company_name", None) or "Our organisation"
    organization_address = getattr(tenant, "organization_address", None)
    # Contact email = main super_admin email (per product decision), falling
    # back to the DPO contact address.
    contact_email = await _resolve_main_super_admin_email(tenant_id) or getattr(
        tenant, "dpo_contact_email", None
    )
    return build_tenant_dpa_content(
        company_name=company_name,
        organization_address=organization_address,
        contact_email=contact_email,
    )


async def retrieve_or_build_tenant_dpa(tenant_id: str) -> Optional[DpaAgreementOut]:
    """Return the tenant's DPA, refreshing it from current tenant fields while
    unaccepted and returning the frozen snapshot once accepted.

    Returns None when no DPA exists and the template asset is unavailable.
    """
    existing = await get_dpa_for_tenant(tenant_id)
    if existing and existing.accepted:
        return existing

    content = await _build_content_for_tenant(tenant_id)
    if content is None:
        # Template not configured yet — return whatever we have (possibly None).
        return existing

    now = int(time.time())
    record = DpaAgreementCreate(
        tenant_id=tenant_id,
        version=_current_dpa_version(),
        title=content["title"],
        summary=content["summary"],
        full_text=content["full_text"],
        body=content["body"],
        accepted=False,
        date_created=getattr(existing, "created_at", None) or now,
        last_updated=now,
    )
    return await upsert_dpa(record)


async def seed_tenant_dpa(tenant_id: str) -> Optional[DpaAgreementOut]:
    """Best-effort seed used by provisioning / backfill. Swallows errors."""
    try:
        return await retrieve_or_build_tenant_dpa(tenant_id)
    except Exception:
        logger.warning("DPA seed failed for tenant_id=%s", tenant_id, exc_info=True)
        return None


async def mark_tenant_dpa_accepted(
    tenant_id: str,
    *,
    actor_id: str,
    accepted_at: Optional[int] = None,
    request_id: Optional[str] = None,
) -> Optional[DpaAgreementOut]:
    """Freeze and mark the tenant's DPA copy accepted.

    Builds/refreshes the copy first (so the frozen snapshot reflects the
    tenant's current details), then stamps acceptance. Returns None when the
    template asset is unavailable. Best-effort audit; never blocks acceptance.
    """
    # Refresh from current fields so the frozen copy is accurate, unless it is
    # already accepted (then we keep the existing frozen body).
    current = await retrieve_or_build_tenant_dpa(tenant_id)
    if current is None:
        return None
    if current.accepted:
        return current

    ts = accepted_at or int(time.time())
    updated = await update_dpa(
        tenant_id,
        DpaAgreementUpdate(
            accepted=True,
            accepted_at=ts,
            accepted_by=actor_id,
            version=_current_dpa_version(),
        ),
    )
    if updated is not None:
        try:
            from services.audit_service import record_audit_event

            await record_audit_event(
                actor_id=actor_id,
                actor_role="super_admin",
                action="tenant_dpa.accepted",
                resource_type="tenant_dpa",
                resource_id=updated.id or "",
                tenant_id=tenant_id,
                details={"version": updated.version, "accepted_at": ts},
                request_id=request_id,
            )
        except Exception:  # pragma: no cover - audit is fire-and-forget
            logger.warning(
                "DPA acceptance audit failed for tenant_id=%s", tenant_id, exc_info=True
            )
    return updated
