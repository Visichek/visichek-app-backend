from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException
from typing import Any, List, Optional

from repositories.tenant_repo import (
    create_tenant,
    get_tenant,
    get_tenants,
    update_tenant,
    delete_tenant,
)
from schemas.tenant_schema import (
    TenantCreate,
    TenantUpdate,
    TenantOut,
    TenantBootstrapRequest,
    TenantWithSummaryOut,
    TenantPlanSummary,
    TenantInfoConfirmRequest,
    TenantInfoConfirmationOut,
)


async def bootstrap_tenant(payload: TenantBootstrapRequest) -> dict:
    """
    Create a new tenant **and** its first super_admin in one atomic step.

    Only application admins call this.  If the system user creation fails the
    tenant is rolled back (deleted) so we never leave an orphaned tenant.

    Returns a dict with keys ``tenant`` (TenantOut) and ``super_admin``
    (SystemUserOut).
    """
    from repositories.system_user_repo import get_system_user
    from schemas.system_user_schema import SystemUserCreate
    from schemas.imports import SystemUserRole, AccountStatus
    from services.system_user_service import add_system_user

    # 1. Check for duplicate company name
    existing = await get_tenant(filter_dict={"company_name": payload.company_name})
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Tenant with this company name already exists",
        )

    # 1B. Cross-border transfer validation
    if hasattr(payload, "country_of_hosting") and payload.country_of_hosting:
        if payload.country_of_hosting.lower() != "nigeria" and not getattr(
            payload, "cross_border_approved", False
        ):
            raise HTTPException(
                status_code=400,
                detail="Cross-border data transfer requires approval. Set cross_border_approved=true or use hosting within Nigeria.",
            )

    # 2. Create the tenant
    tenant_data = TenantCreate(
        company_name=payload.company_name,
        lawful_basis=payload.lawful_basis,
        notice_display_mode=payload.notice_display_mode,
        retention_days=payload.retention_days,
        default_retention_action=payload.default_retention_action,
        dpo_contact_email=payload.dpo_contact_email,
        privacy_policy_url=payload.privacy_policy_url,
        country_of_hosting=payload.country_of_hosting,
        cross_border_approved=payload.cross_border_approved,
    )
    tenant = await create_tenant(tenant_data)

    # 3. Guard: no super_admin should exist for this tenant yet
    existing_super = await get_system_user(
        {
            "tenant_id": tenant.id,
            "role": SystemUserRole.SUPER_ADMIN.value,
        }
    )
    if existing_super:
        raise HTTPException(
            status_code=409,
            detail="This tenant already has a super_admin",
        )

    # 4. Provision a default Headquarters branch BEFORE creating the first
    # super_admin so the user lands on at least one branch (every system_user
    # carries branch_ids — see schemas/system_user_schema.py).
    try:
        from services.branch_service import ensure_default_branch

        hq_branch = await ensure_default_branch(
            tenant_id=tenant.id or "",
            company_name=tenant.company_name or payload.company_name,
        )
    except Exception:
        await delete_tenant({"_id": ObjectId(tenant.id)})
        raise

    # 5. Create the first super_admin system user. The bootstrap path is
    # the only place that mints a super_admin AND knows the tenant has
    # zero existing super_admins, so we set ``is_main_super_admin=True``
    # here directly. The partial-unique index in core/indexes.py keeps
    # this row's uniqueness guarantee across the rest of the tenant's
    # lifetime.
    try:
        super_admin_data = SystemUserCreate(
            tenant_id=tenant.id or "",
            branch_ids=[hq_branch.id] if hq_branch and hq_branch.id else [],
            full_name=payload.admin_full_name,
            email=payload.admin_email,
            role=SystemUserRole.SUPER_ADMIN,
            account_status=AccountStatus.ACTIVE,
            password_hash=payload.admin_password,
            is_main_super_admin=True,
        )
        super_admin = await add_system_user(super_admin_data)
    except Exception:
        # Roll back: remove the orphaned tenant
        await delete_tenant({"_id": ObjectId(tenant.id)})
        raise

    # 5b. Auto-subscribe the new tenant to the Free plan so plan
    # enforcement has something to resolve from the very first request.
    # Best-effort — a failure is logged but does not roll back the
    # tenant; the periodic ``run_full_bootstrap`` re-runs and will
    # backfill any tenant that slipped through.
    try:
        from services.plan_bootstrap import ensure_tenant_default_subscription

        await ensure_tenant_default_subscription(tenant_id=tenant.id or "")
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "free-plan auto-subscribe failed for tenant_id=%s",
            tenant.id,
            exc_info=True,
        )

    # 5c. Register the tenant as a Paystack customer up-front (everyone starts
    # on Free, so this is just a contact record — it does NOT enable charging;
    # that needs a card authorization captured on the first real payment).
    # Best-effort and only when Paystack is configured.
    try:
        from core.payments import PaymentManager
        from core.payments.types import PaymentProviderName

        if PaymentManager.get_instance().has_provider(
            PaymentProviderName.PAYSTACK.value
        ):
            from services.paystack_customer_service import (
                create_or_get_paystack_customer,
            )

            await create_or_get_paystack_customer(
                tenant_id=tenant.id or "",
                email=payload.admin_email,
                name=tenant.company_name or payload.company_name,
            )
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Paystack customer provisioning failed for tenant_id=%s",
            tenant.id,
            exc_info=True,
        )

    # 5. Seed tenant-configurable enums (purpose-of-visit, id_type, ...)
    # so the kiosk has a sensible default picker before the super_admin
    # ever touches the configuration UI. Best-effort — a seed failure
    # is logged but does not roll back the tenant; ``list_enums_for_tenant``
    # auto-seeds on first read as a fallback.
    try:
        from services.tenant_enum_service import seed_tenant_enums

        await seed_tenant_enums(tenant.id or "")
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "tenant_enum seeding failed for tenant_id=%s", tenant.id, exc_info=True
        )

    # 6. Seed the per-tenant agreement copies (DPA + Visitor Privacy Policy)
    # so the kiosk notice is available and the acceptance gate can resolve
    # immediately. Best-effort; the read paths rebuild lazily as a fallback.
    await _seed_tenant_agreements_safe(tenant.id or "")

    return {
        "tenant": tenant,
        "super_admin": super_admin,
    }


async def _seed_tenant_agreements_safe(tenant_id: str) -> None:
    """Best-effort seed of the per-tenant agreement copies on provisioning.

    Builds an (unaccepted) copy of every registered agreement. No-op when a
    master template is absent; the read paths rebuild lazily too. Replaces the
    former separate privacy-notice + DPA seeds (both are now agreements)."""
    if not tenant_id:
        return
    try:
        from services.tenant_agreements.seed import seed_tenant_agreements

        await seed_tenant_agreements(tenant_id)
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "tenant agreements seeding failed for tenant_id=%s",
            tenant_id,
            exc_info=True,
        )


async def add_tenant(
    tenant_data: TenantCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> TenantOut:
    existing = await get_tenant(filter_dict={"company_name": tenant_data.company_name})
    if existing:
        raise HTTPException(
            status_code=409, detail="Tenant with this company name already exists"
        )

    # Cross-border transfer validation
    if hasattr(tenant_data, "country_of_hosting") and tenant_data.country_of_hosting:
        if tenant_data.country_of_hosting.lower() != "nigeria" and not getattr(
            tenant_data, "cross_border_approved", False
        ):
            raise HTTPException(
                status_code=400,
                detail="Cross-border data transfer requires approval. Set cross_border_approved=true or use hosting within Nigeria.",
            )

    tenant = await create_tenant(tenant_data, preassigned_id=preassigned_id)

    # Every tenant must have an active subscription — auto-enrol on the
    # Free plan immediately so plan enforcement, quota tracking, and the
    # tenant dashboard all have a plan to resolve from the very first
    # request. Idempotent: ``ensure_tenant_default_subscription`` is a
    # no-op if the tenant already has an active or trialing subscription
    # (the ``bootstrap_tenant`` path also calls it explicitly). Best
    # effort — a failure here logs but does not roll back the tenant,
    # and the lazy fallback in ``PlanEnforcementMiddleware`` will heal
    # the gap on the next request.
    try:
        from services.plan_bootstrap import ensure_tenant_default_subscription

        await ensure_tenant_default_subscription(tenant_id=tenant.id or "")
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "free-plan auto-subscribe failed for tenant_id=%s",
            tenant.id,
            exc_info=True,
        )

    # Seed the per-tenant agreement copies (DPA + Visitor Privacy Policy).
    # Covers the self-onboarding accept / partial-accept paths which both
    # provision the tenant through add_tenant. Best-effort; rebuilt on read.
    await _seed_tenant_agreements_safe(tenant.id or "")

    return tenant


async def retrieve_tenant_by_id(tenant_id: str) -> TenantOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await get_tenant({"_id": ObjectId(tenant_id)})
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return result


async def retrieve_tenants(start=0, stop=100) -> List[TenantOut]:
    return await get_tenants(start=start, stop=stop)


def _build_plan_summary(plan_data: dict | None) -> TenantPlanSummary | None:
    if not plan_data:
        return None
    return TenantPlanSummary(
        plan_id=plan_data.get("plan_id"),
        plan_name=plan_data.get("plan_name"),
        plan_display_name=plan_data.get("plan_display_name"),
        plan_tier=plan_data.get("tier"),
        subscription_id=plan_data.get("subscription_id"),
        subscription_status=plan_data.get("subscription_status"),
        billing_cycle=plan_data.get("billing_cycle"),
        effective_price=plan_data.get("effective_price"),
        currency="NGN",
        current_period_end=plan_data.get("current_period_end"),
        trial_ends_at=plan_data.get("trial_ends_at"),
        entity_caps=plan_data.get("tenant_caps"),
    )


async def _enrich_tenant(tenant: TenantOut) -> TenantWithSummaryOut:
    from services.plan_cache_service import resolve_tenant_plan

    plan_data = await resolve_tenant_plan(tenant.id or "")
    data = tenant.model_dump(by_alias=False)
    data["plan_summary"] = _build_plan_summary(plan_data)
    return TenantWithSummaryOut(**data)


async def retrieve_tenants_with_summary(
    start=0, stop=100
) -> List[TenantWithSummaryOut]:
    """List tenants with their plan summary.

    Uses ``resolve_tenant_plans_bulk`` so the whole page is served in a
    constant number of round trips: one Redis ``MGET`` + at most one
    ``subscriptions`` find + one ``plans`` find + one pipelined cache write,
    regardless of how many tenants are returned. Previously this did N sync
    Redis ``GET``s and 2N Mongo queries under ``asyncio.gather``, which
    serialised on the event loop.
    """
    from services.plan_cache_service import resolve_tenant_plans_bulk

    tenants = await get_tenants(start=start, stop=stop)
    if not tenants:
        return []

    tenant_ids = [t.id for t in tenants if t.id]
    plans_by_tenant = await resolve_tenant_plans_bulk(tenant_ids)

    results: List[TenantWithSummaryOut] = []
    for tenant in tenants:
        plan_data = plans_by_tenant.get(tenant.id or "")
        data = tenant.model_dump(by_alias=False)
        data["plan_summary"] = _build_plan_summary(plan_data)
        results.append(TenantWithSummaryOut(**data))
    return results


async def retrieve_tenant_by_id_with_summary(tenant_id: str) -> TenantWithSummaryOut:
    tenant = await retrieve_tenant_by_id(tenant_id)
    return await _enrich_tenant(tenant)


async def update_tenant_by_id(tenant_id: str, tenant_data: TenantUpdate) -> TenantOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await update_tenant({"_id": ObjectId(tenant_id)}, tenant_data)
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found or update failed")
    return result


async def remove_tenant(tenant_id: str):
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await delete_tenant({"_id": ObjectId(tenant_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")


# ---------------------------------------------------------------------------
# First-login onboarding info confirmation (tenant super_admin self-service)
# ---------------------------------------------------------------------------


async def _load_onboarding_context(tenant_id: str) -> Optional[Any]:
    """Best-effort fetch of the onboarding submission tied to a tenant.

    Returns the submission (for its verbatim form payload + labels + order)
    or ``None`` for tenants with no submission record — e.g. those created
    via the legacy bootstrap path. Never raises: the confirmation screen
    must still render even if the submission lookup fails.
    """
    try:
        from repositories.onboarding_submission_repo import get_submission

        return await get_submission({"tenant_id": tenant_id})
    except Exception:
        return None


# Version of VisiChek's Data Processing Agreement currently in force. Bump this
# when the DPA text changes so re-acceptance can be required.
CURRENT_DPA_VERSION = "1.0"


def _build_confirmation_out(
    tenant: TenantOut, submission: Optional[Any]
) -> TenantInfoConfirmationOut:
    return TenantInfoConfirmationOut(
        tenant_id=tenant.id or "",
        company_name=tenant.company_name,
        organization_address=getattr(tenant, "organization_address", None),
        dpo_contact_email=tenant.dpo_contact_email,
        privacy_policy_url=tenant.privacy_policy_url,
        country_of_hosting=tenant.country_of_hosting,
        onboarding_info_confirmed=tenant.onboarding_info_confirmed,
        onboarding_info_confirmed_at=tenant.onboarding_info_confirmed_at,
        dpa_accepted=getattr(tenant, "dpa_accepted", False) or False,
        dpa_accepted_at=getattr(tenant, "dpa_accepted_at", None),
        dpa_version=getattr(tenant, "dpa_version", None),
        onboarding_submission_id=getattr(submission, "id", None),
        onboarding_fields=dict(getattr(submission, "payload", {}) or {}),
        onboarding_field_labels=dict(getattr(submission, "field_labels", {}) or {}),
        onboarding_field_order=list(getattr(submission, "field_order", []) or []),
    )


async def get_tenant_info_confirmation(*, tenant_id: str) -> TenantInfoConfirmationOut:
    """Build the first-login review payload for a tenant's super_admin."""
    tenant = await retrieve_tenant_by_id(tenant_id)
    submission = await _load_onboarding_context(tenant_id)
    return _build_confirmation_out(tenant, submission)


async def confirm_tenant_info(
    payload: TenantInfoConfirmRequest,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str = "super_admin",
    request_id: Optional[str] = None,
) -> TenantInfoConfirmationOut:
    """Apply any edits, mark the tenant's onboarding info confirmed, audit it.

    Runs synchronously (mirrors ``POST /v1/onboarding/me/complete``): the
    first-login UX needs the confirmed state back in the response, and this
    is a one-shot self-service step rather than a high-volume mutation.
    """
    import time

    from core.queue.entity_cache import invalidate_entity
    from services.audit_service import record_audit_event

    before = await retrieve_tenant_by_id(tenant_id)

    edits = payload.model_dump(exclude_none=True)

    # DPA acceptance is request-only signalling — pull it out of the editable
    # company-field diff and translate it into system-managed tenant fields
    # (dpa_accepted_by / dpa_version are never client-supplied). The FE only
    # ever sends dpa_accepted=true; a false/missing value is ignored.
    dpa_accepted = edits.pop("dpa_accepted", None)
    dpa_accepted_at = edits.pop("dpa_accepted_at", None)

    changes: dict[str, Any] = {}
    for key, new_value in edits.items():
        old_value = getattr(before, key, None)
        if old_value != new_value:
            changes[key] = {"from": old_value, "to": new_value}

    dpa_update: dict[str, Any] = {}
    if dpa_accepted and not getattr(before, "dpa_accepted", False):
        dpa_update = {
            "dpa_accepted": True,
            "dpa_accepted_at": dpa_accepted_at or int(time.time()),
            "dpa_accepted_by": actor_id,
            "dpa_version": CURRENT_DPA_VERSION,
        }
        changes["dpa_accepted"] = {"from": False, "to": True}

    update = TenantUpdate(
        **edits,
        **dpa_update,
        onboarding_info_confirmed=True,
        onboarding_info_confirmed_at=int(time.time()),
    )
    updated = await update_tenant_by_id(tenant_id=tenant_id, tenant_data=update)

    # Sync path bypasses the write queue, so invalidate caches inline.
    invalidate_entity("tenant", tenant_id)
    try:
        from core.queue.manager import QueueManager

        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": "", "resource": "tenants.list"},
        )
    except Exception:
        pass

    # If the DPA was just accepted, freeze the tenant's DPA agreement copy —
    # built from the company details we just saved (incl. organization_address)
    # — as the immutable record of what was agreed, and clear the acceptance
    # gate. Best-effort: a missing master or transient error must never block
    # the confirmation flow.
    if dpa_update:
        try:
            from services.tenant_agreement_service import mark_accepted

            await mark_accepted(
                tenant_id,
                "dpa",
                actor_id=actor_id,
                actor_role=actor_role,
                accepted_at=dpa_update.get("dpa_accepted_at"),
                request_id=request_id,
            )
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "DPA agreement acceptance failed for tenant_id=%s",
                tenant_id,
                exc_info=True,
            )

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="tenant.onboarding_info_confirmed",
            resource_type="tenant",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            details={"changes": changes},
            request_id=request_id,
        )
    except Exception:
        pass

    submission = await _load_onboarding_context(tenant_id)
    return _build_confirmation_out(updated, submission)
