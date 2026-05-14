"""KYC orchestration service.

Bridges the ``core/kyc`` provider abstraction and the check-in state
machine. Three things live here:

1. :func:`kyc_available_for_tenant` — the decision tree: plan tier
   gates whether KYC is offered at all; tenant settings decide whether
   it's required.
2. :func:`initiate_kyc_for_checkin` — creates a ``kyc_verifications``
   row, calls the provider for widget config, and parks the check-in
   in ``PENDING_VERIFICATION``.
3. :func:`process_webhook_event` / :func:`finalize_kyc` — moves a
   check-in from ``PENDING_VERIFICATION`` to ``PENDING_APPROVAL`` (success) or
   ``REJECTED`` (hard failure / explicit denial) when the provider
   webhook lands.
"""

from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode, resource_not_found
from core.kyc import KYCManager
from core.kyc.types import (
    KYCInitiateRequest,
    KYCInitiateResponse,
    KYCVerificationDetails,
    KYCWebhookEvent,
)
from core.settings import get_settings
from repositories.checkin_repo import (
    get_checkin,
    update_checkin as repo_update_checkin,
)
from repositories.kyc_repo import (
    create_kyc_verification,
    get_kyc_by_checkin,
    get_kyc_by_reference,
    is_webhook_event_processed,
    record_webhook_event,
    update_kyc_verification,
)
from schemas.checkin_schema import CheckinUpdate
from schemas.imports import CheckinState, KYCStatus
from schemas.kyc_schema import (
    KYCInitiateResponseOut,
    KYCStatusOut,
    KYCVerificationCreate,
    KYCVerificationOut,
    KYCVerificationUpdate,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


# ── Plan / settings gating ───────────────────────────────────────────

KYC_SAMPLE_PATH = "/v1/kyc/checkins/x/initiate"
"""Representative request path used to decide whether a plan's
feature_rules deny KYC. ``_kyc_enabled_in_plan`` runs this through
``fnmatch`` against every ``enabled=False`` rule — if any rule matches,
the plan blocks KYC; otherwise the plan grants it."""


def _kyc_enabled_in_plan(plan_data: dict) -> bool:
    """Return ``True`` if the tenant's resolved plan grants KYC.

    Mirrors :class:`PlanEnforcementMiddleware` semantics: a plan grants
    KYC unless it carries a feature_rule whose ``endpoint_pattern``
    matches a KYC request AND has ``enabled=False``. "No matching rule"
    means allowed — the same default the middleware applies to every
    other endpoint (see ``config/plan_tiers.py``).

    Free / Starter plans deny ``/v1/kyc*`` and ``/v1/kyc/*`` explicitly,
    so they resolve to ``False`` here. Premium / Enterprise carry no
    KYC deny rule and resolve to ``True``.
    """
    import fnmatch

    for rule in plan_data.get("feature_rules", []) or []:
        if rule.get("enabled") is False and fnmatch.fnmatch(
            KYC_SAMPLE_PATH, rule.get("endpoint_pattern", "")
        ):
            return False
    return True


async def kyc_available_for_tenant(
    tenant_id: str,
) -> tuple[bool, bool, str]:
    """Resolve KYC availability + requiredness for a tenant.

    Returns ``(available, required, provider_name)``:

    * ``available`` — plan tier grants KYC and the provider is
      configured. The kiosk shows the verify CTA.
    * ``required`` — visitors cannot skip the verify step.
    * ``provider_name`` — string the kiosk passes when initiating.

    Failures resolving the plan or settings degrade to "not available"
    rather than crashing the kiosk submit path.
    """
    from services.plan_cache_service import resolve_tenant_plan
    from services.tenant_settings_service import retrieve_or_create_tenant_settings

    try:
        plan_data = await resolve_tenant_plan(tenant_id)
    except Exception:
        logger.warning(
            "kyc_available_for_tenant: plan lookup failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        plan_data = None

    if not plan_data:
        return (False, False, "dojah")

    enabled_in_plan = _kyc_enabled_in_plan(plan_data)
    if not enabled_in_plan:
        return (False, False, "dojah")

    try:
        settings = await retrieve_or_create_tenant_settings(tenant_id)
    except Exception:
        logger.warning(
            "kyc_available_for_tenant: settings lookup failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
        return (True, False, "dojah")

    provider_name = (
        settings.kyc_provider.value
        if hasattr(settings.kyc_provider, "value")
        else str(settings.kyc_provider or "dojah")
    )
    if not KYCManager.get_instance().has_provider(provider_name):
        # Plan grants KYC but the provider hasn't been configured —
        # treat as unavailable so the kiosk doesn't render a CTA that
        # would only error.
        return (False, False, provider_name)

    return (True, bool(settings.kyc_required), provider_name)


# ── Initiate ────────────────────────────────────────────────────────

async def initiate_kyc_for_checkin(
    *,
    checkin_id: str,
) -> KYCInitiateResponseOut:
    """Start a KYC session for a pending check-in.

    Idempotent: re-calling for the same ``checkin_id`` returns the
    existing record's widget config (verifications cost the tenant
    money — we don't want a refresh-driven kiosk to spawn duplicates).
    """
    if not ObjectId.is_valid(checkin_id):
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    if checkin.state not in (CheckinState.PENDING_VERIFICATION, CheckinState.PENDING_APPROVAL):
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"Cannot start KYC on check-in in state '{checkin.state}'. "
                "Only freshly submitted check-ins are eligible."
            ),
        )

    available, _required, provider_name = await kyc_available_for_tenant(
        checkin.tenant_id
    )
    if not available:
        raise AppException(
            status_code=402,
            code=ErrorCode.FEATURE_DISABLED,
            message=(
                "KYC is not available on the tenant's current plan, or "
                "the provider has not been configured."
            ),
        )

    existing = await get_kyc_by_checkin(checkin_id)
    if existing is not None and existing.status in (
        KYCStatus.PENDING,
        KYCStatus.ONGOING,
    ):
        provider = KYCManager.get_instance().get_provider(provider_name)
        widget_config = await _build_widget_config(provider, checkin, existing)
        return KYCInitiateResponseOut(
            reference_id=existing.reference_id or checkin_id,
            provider=provider_name,
            widget_config=widget_config,
        )

    # Pull visitor identity off the check-in's referenced visitor doc.
    from repositories.visitor_repo import get_visitor

    visitor = await get_visitor({"_id": checkin.visitor_id})
    full_name = visitor.full_name if visitor else "Visitor"
    phone = (visitor.phone if visitor else None) or ""
    email = visitor.email if visitor else None

    provider = KYCManager.get_instance().get_provider(provider_name)
    init_resp: KYCInitiateResponse = await provider.initiate(
        KYCInitiateRequest(
            tenant_id=checkin.tenant_id,
            checkin_id=checkin_id,
            visitor_full_name=full_name,
            visitor_phone=phone,
            visitor_email=email,
            metadata={"visitor_id": checkin.visitor_id},
        )
    )

    record = await create_kyc_verification(
        KYCVerificationCreate(
            tenant_id=checkin.tenant_id,
            checkin_id=checkin_id,
            visitor_id=checkin.visitor_id,
            provider=provider_name,
            reference_id=init_resp.reference_id,
            status=KYCStatus.ONGOING,
        )
    )

    # Park the check-in in PENDING_VERIFICATION so receptionists don't see it
    # in their approval queue while the widget is running.
    if checkin.state != CheckinState.PENDING_VERIFICATION:
        await repo_update_checkin(
            checkin_id, CheckinUpdate(state=CheckinState.PENDING_VERIFICATION)
        )

    await record_audit_event(
        actor_id="",
        actor_role="public",
        action="kyc.initiated",
        resource_type="checkin",
        resource_id=checkin_id,
        tenant_id=checkin.tenant_id,
        details={"provider": provider_name, "reference_id": init_resp.reference_id},
    )

    _ = record  # retained for tests inspecting the freshly-created row
    return KYCInitiateResponseOut(
        reference_id=init_resp.reference_id,
        provider=provider_name,
        widget_config=init_resp.widget_config,
        expires_at=init_resp.expires_at,
    )


async def _build_widget_config(provider, checkin, existing: KYCVerificationOut) -> dict:
    """Rebuild widget config for an existing pending verification.

    Used when a kiosk re-calls initiate after a network blip — we
    return the same config Dojah would have built originally so the
    visitor can resume.
    """
    from repositories.visitor_repo import get_visitor

    visitor = await get_visitor({"_id": checkin.visitor_id})
    init = await provider.initiate(
        KYCInitiateRequest(
            tenant_id=checkin.tenant_id,
            checkin_id=str(checkin.id or ""),
            visitor_full_name=visitor.full_name if visitor else "Visitor",
            visitor_phone=(visitor.phone if visitor else None) or "",
            visitor_email=visitor.email if visitor else None,
            metadata={
                "visitor_id": checkin.visitor_id,
                "resume_for_reference": existing.reference_id or "",
            },
        )
    )
    return init.widget_config


# ── Skip (visitor opts out) ─────────────────────────────────────────

async def skip_kyc_for_checkin(
    *,
    checkin_id: str,
    reason: Optional[str] = None,
) -> KYCStatusOut:
    """Visitor declined to verify on a tenant where KYC is optional."""
    if not ObjectId.is_valid(checkin_id):
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    available, required, _provider = await kyc_available_for_tenant(
        checkin.tenant_id
    )
    if available and required:
        raise AppException(
            status_code=403,
            code=ErrorCode.FEATURE_DISABLED,
            message="This tenant requires KYC verification — skip is not allowed.",
        )

    # Whether or not a verification row already exists, transition to
    # PENDING_APPROVAL so receptionists see the check-in in their queue.
    existing = await get_kyc_by_checkin(checkin_id)
    if existing is not None:
        await update_kyc_verification(
            {"checkin_id": checkin_id},
            KYCVerificationUpdate(
                status=KYCStatus.SKIPPED,
                failure_reason=reason or "visitor opted to skip",
            ),
        )
    if checkin.state == CheckinState.PENDING_VERIFICATION:
        await repo_update_checkin(
            checkin_id, CheckinUpdate(state=CheckinState.PENDING_APPROVAL)
        )

    await record_audit_event(
        actor_id="",
        actor_role="public",
        action="kyc.skipped",
        resource_type="checkin",
        resource_id=checkin_id,
        tenant_id=checkin.tenant_id,
        details={"reason": reason} if reason else None,
    )

    # Notify the receptionist queue now that the check-in is visible.
    try:
        from services.notification_service import notify_checkin_pending_approval
        from repositories.visitor_repo import get_visitor

        visitor = await get_visitor({"_id": checkin.visitor_id})
        await notify_checkin_pending_approval(
            tenant_id=checkin.tenant_id,
            checkin_id=checkin_id,
            visitor_name=visitor.full_name if visitor else "Visitor",
            verified=False,
            purpose=checkin.purpose.purpose,
            host_employee_id="",
        )
    except Exception:
        logger.warning(
            "skip_kyc_for_checkin: notify failed checkin_id=%s",
            checkin_id,
            exc_info=True,
        )

    return KYCStatusOut(
        checkin_id=checkin_id,
        reference_id=existing.reference_id if existing else None,
        status=KYCStatus.SKIPPED,
        failure_reason=reason,
    )


# ── Webhook handler ─────────────────────────────────────────────────

async def process_webhook_event(
    *,
    body: bytes,
    headers: dict[str, str],
) -> dict:
    """Verify, persist, and apply the webhook event.

    Returns a dict suitable for the public response:
    ``{ accepted: True/False, status: ..., reason: ... }``. We always
    return 200 on a *parsable* event so Dojah doesn't keep retrying;
    rejected signatures are logged and recorded as
    ``processing_status=rejected_signature`` in ``kyc_webhook_events``
    for audit.
    """
    settings = get_settings()
    provider = KYCManager.get_instance().get_provider("dojah")

    event: KYCWebhookEvent = provider.parse_webhook(body=body, headers=headers)

    # Reject events that don't carry the body-bound signature when
    # required. ``signature_valid`` is true for either v1 or v2 in
    # ``DojahKYCProvider``; if the operator wants strict v1, they set
    # the env var and we re-validate here.
    if settings.dojah_require_v1_signature:
        v1 = headers.get("x-dojah-signature") or headers.get("X-Dojah-Signature")
        if not v1 or not event.signature_valid:
            await record_webhook_event(
                provider=event.provider,
                event_id=event.event_id,
                event_type=event.event_type,
                reference_id=event.reference_id or None,
                raw_payload=event.raw_payload,
                signature_valid=event.signature_valid,
                processing_status="rejected_signature",
                error="Missing or invalid x-dojah-signature",
            )
            logger.warning(
                "dojah webhook: rejected (signature) event=%s ref=%s",
                event.event_id,
                event.reference_id,
            )
            return {"accepted": False, "reason": "invalid_signature"}

    if not event.signature_valid:
        await record_webhook_event(
            provider=event.provider,
            event_id=event.event_id,
            event_type=event.event_type,
            reference_id=event.reference_id or None,
            raw_payload=event.raw_payload,
            signature_valid=False,
            processing_status="rejected_signature",
            error="No matching signature header",
        )
        return {"accepted": False, "reason": "invalid_signature"}

    # Idempotency — silently no-op duplicates.
    if await is_webhook_event_processed(
        provider=event.provider, event_id=event.event_id
    ):
        return {"accepted": True, "duplicate": True}

    await record_webhook_event(
        provider=event.provider,
        event_id=event.event_id,
        event_type=event.event_type,
        reference_id=event.reference_id or None,
        raw_payload=event.raw_payload,
        signature_valid=True,
        processing_status="received",
    )

    if not event.details or not event.reference_id:
        # Lifecycle events with no verification body (e.g. session
        # opened) — recorded but no state change.
        return {"accepted": True, "status": "noop"}

    finalised = await finalize_kyc(
        reference_id=event.reference_id,
        details=event.details,
    )
    return {"accepted": True, "status": finalised.status.value}


async def finalize_kyc(
    *,
    reference_id: str,
    details: KYCVerificationDetails,
) -> KYCVerificationOut:
    """Apply provider results to the verification row + check-in.

    Pure inputs/outputs so the polling fallback (``GET /v1/kyc/status``)
    can call this with the polled details too.
    """
    record = await get_kyc_by_reference(reference_id)
    if record is None:
        # Webhook arrived before the kiosk's initiate completed (rare
        # — Dojah's widget creates the verification client-side). Drop
        # in a stub row so the audit trail still has a ref point.
        logger.warning(
            "finalize_kyc: no verification row for reference_id=%s — creating stub",
            reference_id,
        )
        # Without a checkin_id we can't progress the state machine,
        # so the stub is a dead-end record.
        record = await create_kyc_verification(
            KYCVerificationCreate(
                tenant_id="",
                checkin_id="",
                provider="dojah",
                reference_id=reference_id,
                status=_status_from_details(details),
            )
        )
        return record

    new_status = _status_from_details(details)
    updated = await update_kyc_verification(
        {"checkin_id": record.checkin_id},
        KYCVerificationUpdate(
            reference_id=reference_id,
            status=new_status,
            confidence=details.confidence,
            extracted_full_name=details.extracted_full_name,
            extracted_dob=details.extracted_dob,
            extracted_id_number=details.extracted_id_number,
            extracted_id_type=details.extracted_id_type,
            selfie_url=details.selfie_url,
            id_image_url=details.id_image_url,
            failure_reason=details.failure_reason,
            raw_payload=details.data,
        ),
    )
    if updated is None:
        return record

    if record.checkin_id:
        await _apply_to_checkin(updated)
    return updated


def _status_from_details(details: KYCVerificationDetails) -> KYCStatus:
    if details.status == "success":
        return KYCStatus.SUCCESS
    if details.status == "failed":
        return KYCStatus.FAILED
    if details.status == "expired":
        return KYCStatus.EXPIRED
    return KYCStatus.ONGOING


async def _apply_to_checkin(record: KYCVerificationOut) -> None:
    """State-machine transitions driven by KYC outcome."""
    checkin = await get_checkin({"_id": record.checkin_id})
    if checkin is None:
        return

    if record.status == KYCStatus.SUCCESS:
        # Move to PENDING_APPROVAL only if we're still parked. If a
        # receptionist already approved (rare, but theoretically possible
        # for tenants where KYC isn't required), don't unwind.
        if checkin.state == CheckinState.PENDING_VERIFICATION:
            await repo_update_checkin(
                record.checkin_id,
                CheckinUpdate(state=CheckinState.PENDING_APPROVAL, verified=True),
            )
            await _emit_pending_notification(checkin, verified=True)
        else:
            await repo_update_checkin(
                record.checkin_id, CheckinUpdate(verified=True)
            )

        # Update the visitor profile's verification metadata.
        try:
            from bson import ObjectId as _ObjectId
            from repositories.visitor_profile_repo import (
                get_visitor_profile_by_phone,
                update_visitor_profile,
            )
            from schemas.visitor_profile_schema import VisitorProfileUpdate
            from repositories.visitor_repo import get_visitor

            visitor = await get_visitor({"_id": checkin.visitor_id})
            phone = visitor.phone if visitor else None
            if phone:
                profile = await get_visitor_profile_by_phone(
                    tenant_id=checkin.tenant_id, phone=phone
                )
                if profile and profile.id and _ObjectId.is_valid(profile.id):
                    import time as _time

                    await update_visitor_profile(
                        {
                            "_id": _ObjectId(profile.id),
                            "tenant_id": checkin.tenant_id,
                        },
                        VisitorProfileUpdate(
                            verification_status="verified",
                            verification_method=record.extracted_id_type
                            or "dojah_kyc",
                            last_verification_date=int(_time.time()),
                            id_type=record.extracted_id_type,
                            id_number=record.extracted_id_number,
                        ),
                    )
        except Exception:
            logger.warning(
                "kyc.finalize: visitor_profile sync failed checkin=%s",
                record.checkin_id,
                exc_info=True,
            )
    elif record.status == KYCStatus.FAILED:
        if checkin.state == CheckinState.PENDING_VERIFICATION:
            await repo_update_checkin(
                record.checkin_id,
                CheckinUpdate(
                    state=CheckinState.REJECTED,
                    rejection_reason=record.failure_reason
                    or "KYC verification failed",
                ),
            )
    elif record.status == KYCStatus.EXPIRED:
        if checkin.state == CheckinState.PENDING_VERIFICATION:
            await repo_update_checkin(
                record.checkin_id,
                CheckinUpdate(
                    state=CheckinState.REJECTED,
                    rejection_reason="KYC verification expired",
                ),
            )

    await record_audit_event(
        actor_id="",
        actor_role="system",
        action=f"kyc.{record.status.value}",
        resource_type="checkin",
        resource_id=record.checkin_id,
        tenant_id=checkin.tenant_id,
        details={
            "reference_id": record.reference_id,
            "provider": record.provider,
            "confidence": record.confidence,
            "failure_reason": record.failure_reason,
        },
    )


async def _emit_pending_notification(checkin, *, verified: bool) -> None:
    try:
        from repositories.visitor_repo import get_visitor
        from services.notification_service import notify_checkin_pending_approval

        visitor = await get_visitor({"_id": checkin.visitor_id})
        await notify_checkin_pending_approval(
            tenant_id=checkin.tenant_id,
            checkin_id=str(checkin.id or ""),
            visitor_name=visitor.full_name if visitor else "Visitor",
            verified=verified,
            purpose=checkin.purpose.purpose,
            host_employee_id="",
        )
    except Exception:
        logger.warning(
            "kyc.finalize: notify failed checkin=%s",
            getattr(checkin, "id", None),
            exc_info=True,
        )


# ── Status polling ──────────────────────────────────────────────────

async def get_kyc_status_for_checkin(checkin_id: str) -> KYCStatusOut:
    """Return the current KYC status without polling Dojah.

    The kiosk uses this to flip from spinner → success/failure when
    the webhook landed before the widget's onSuccess fires (or vice
    versa).
    """
    if not ObjectId.is_valid(checkin_id):
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)
    record = await get_kyc_by_checkin(checkin_id)
    if record is None:
        return KYCStatusOut(
            checkin_id=checkin_id,
            reference_id=None,
            status=KYCStatus.PENDING,
        )
    return KYCStatusOut(
        checkin_id=checkin_id,
        reference_id=record.reference_id,
        status=record.status,
        failure_reason=record.failure_reason,
    )
