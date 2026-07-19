from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bson import ObjectId

from core.email_utils import normalize_email
from core.errors import AppException, ErrorCode
from core.settings import get_settings
from core.test_mode import is_test_email, issue_temp_password
from repositories.onboarding_submission_repo import (
    count_submissions,
    create_submission,
    distinct_marketing_opt_in_emails,
    get_submission_by_id,
    list_submissions,
    update_submission_by_id,
)
from repositories.tenant_repo import delete_tenant
from schemas.imports import (
    AccountStatus,
    LawfulBasis,
    NoticeDisplayMode,
    OnboardingStatus,
    SystemUserRole,
)
from schemas.onboarding_submission_schema import (
    EMAIL_KEYS,
    NAME_KEYS,
    ORGANIZATION_KEYS,
    OnboardingAcceptOut,
    OnboardingAcceptRequest,
    OnboardingCompleteRequest,
    OnboardingPartialAcceptRequest,
    OnboardingPendingFieldsOut,
    OnboardingRejectRequest,
    OnboardingSubmissionCreate,
    OnboardingSubmissionOut,
    OnboardingSubmissionRequest,
    OnboardingSubmissionUpdate,
)
from schemas.tenant_schema import TenantCreate
from security.temp_password import issued_at_now as _temp_password_issued_at
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public submission flow
# ---------------------------------------------------------------------------


async def submit_onboarding(
    request: OnboardingSubmissionRequest,
    *,
    client_ip: Optional[str],
    user_agent: Optional[str],
) -> OnboardingSubmissionOut:
    """Verify Turnstile, run the extractor, and persist the row.

    Gating:
      * If ``platform_settings.self_onboarding_enabled`` is False the endpoint
        rejects with ``403 self_onboarding_disabled``. The endpoint itself
        stays mounted so the frontend can detect the disabled state via the
        error code.
    """
    await _ensure_self_onboarding_enabled()

    extracted = _extract_indexed_fields(request.payload)

    # Test accounts (non-production only) skip the Turnstile round-trip so
    # automated E2E runs can drive this endpoint without a real browser
    # challenge. is_test_email() is a hard False in production.
    if is_test_email(extracted.get("email")):
        logger.info(
            "Test-email onboarding submission (%s); skipping Turnstile",
            extracted.get("email"),
        )
    else:
        await _verify_turnstile_token(request.turnstile_token, client_ip=client_ip)

    create_data = OnboardingSubmissionCreate(
        form_version=request.form_version,
        payload=request.payload,
        field_labels=request.field_labels,
        field_order=request.field_order,
        email=extracted["email"],
        full_name=extracted["full_name"],
        organization_name=extracted["organization_name"],
        status=OnboardingStatus.NEW,
        turnstile_verified=True,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    submission = await create_submission(create_data)

    # Fire-and-forget audit + admin notification
    try:
        await record_audit_event(
            actor_id="public",
            actor_role="anonymous",
            action="onboarding.submitted",
            resource_type="onboarding_submission",
            resource_id=submission.id or "",
            details={
                "email": submission.email,
                "organization_name": submission.organization_name,
            },
        )
    except Exception:
        pass

    from services.notification_service import (
        notify_onboarding_submission_received,
    )

    await notify_onboarding_submission_received(
        submission_id=submission.id or "",
        organization_name=submission.organization_name,
        full_name=submission.full_name,
        email=submission.email,
    )

    return submission


# ---------------------------------------------------------------------------
# Admin actions (list / get / reject / accept / partial-accept / archive)
# ---------------------------------------------------------------------------


async def list_onboarding_submissions(
    *,
    status: Optional[OnboardingStatus] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[OnboardingSubmissionOut], int]:
    filter_dict: Dict[str, Any] = {}
    if status is not None:
        filter_dict["status"] = status.value
    items = await list_submissions(filter_dict, skip=skip, limit=limit)
    total = await count_submissions(filter_dict)
    return items, total


async def list_marketing_opt_in_emails() -> Tuple[List[str], int]:
    """Return the deduplicated email list for every onboarding submission whose
    ``marketing_opt_in`` field is set to an affirmative value.

    Emails are stored already-normalized on the submission row (see
    ``_extract_indexed_fields``), so distinct() at the DB layer is enough to
    drop alias / casing duplicates across multiple submissions for the same
    person. Submissions without an extracted email are skipped.
    """
    emails = await distinct_marketing_opt_in_emails()
    return emails, len(emails)


async def retrieve_onboarding_submission(submission_id: str) -> OnboardingSubmissionOut:
    submission = await get_submission_by_id(submission_id)
    if submission is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Onboarding submission not found",
            details={"submission_id": submission_id},
        )
    return submission


async def reject_onboarding_submission(
    submission_id: str,
    payload: OnboardingRejectRequest,
    *,
    actor_id: str,
) -> OnboardingSubmissionOut:
    submission = await retrieve_onboarding_submission(submission_id)
    _ensure_in_terminal_window(submission, "reject")

    update = OnboardingSubmissionUpdate(
        status=OnboardingStatus.REJECTED,
        review_notes=payload.review_notes,
        reviewed_by=actor_id,
        reviewed_at=int(time.time()),
    )
    updated = await update_submission_by_id(submission_id, update)
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to reject onboarding submission",
        )

    await _queue_status_email(
        updated,
        template_key="onboarding_rejected",
        review_notes=payload.review_notes,
    )

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action="onboarding.rejected",
            resource_type="onboarding_submission",
            resource_id=submission_id,
            details={"review_notes": payload.review_notes},
        )
    except Exception:
        pass
    return updated


async def archive_onboarding_submission(
    submission_id: str,
    *,
    actor_id: str,
) -> OnboardingSubmissionOut:
    submission = await retrieve_onboarding_submission(submission_id)

    update = OnboardingSubmissionUpdate(
        status=OnboardingStatus.ARCHIVED,
        reviewed_by=actor_id,
        reviewed_at=int(time.time()),
    )
    updated = await update_submission_by_id(submission_id, update)
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to archive onboarding submission",
        )

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action="onboarding.archived",
            resource_type="onboarding_submission",
            resource_id=submission_id,
            details={"prior_status": submission.status.value},
        )
    except Exception:
        pass
    return updated


async def accept_onboarding_submission(
    submission_id: str,
    payload: OnboardingAcceptRequest,
    *,
    actor_id: str,
) -> OnboardingAcceptOut:
    """Accept fully — provision tenant + super_admin."""
    return await _accept_internal(
        submission_id=submission_id,
        payload=payload,
        actor_id=actor_id,
        pending_field_keys=[],
        target_status=OnboardingStatus.ACCEPTED,
        email_template="onboarding_accepted",
    )


async def partial_accept_onboarding_submission(
    submission_id: str,
    payload: OnboardingPartialAcceptRequest,
    *,
    actor_id: str,
) -> OnboardingAcceptOut:
    """Accept partially — same provisioning, plus a list of fields the tenant
    must fill in via /v1/onboarding/me/complete."""
    return await _accept_internal(
        submission_id=submission_id,
        payload=payload,
        actor_id=actor_id,
        pending_field_keys=list(payload.pending_field_keys),
        target_status=OnboardingStatus.PARTIAL_ACCEPTED,
        email_template="onboarding_partial_accepted",
    )


# ---------------------------------------------------------------------------
# Self-completion (used by the newly created super_admin to fill in fields)
# ---------------------------------------------------------------------------


async def get_pending_fields_for_user(
    *,
    user_id: str,
    tenant_id: Optional[str],
) -> OnboardingPendingFieldsOut:
    submission = await _find_submission_for_user(
        user_id=user_id, tenant_id=tenant_id, allow_completed=True
    )
    return OnboardingPendingFieldsOut(
        submission_id=submission.id or "",
        status=submission.status,
        tenant_id=submission.tenant_id,
        pending_field_keys=list(submission.pending_field_keys),
        pending_field_labels=dict(submission.pending_field_labels),
        review_notes=submission.review_notes,
    )


async def complete_onboarding_for_user(
    payload: OnboardingCompleteRequest,
    *,
    user_id: str,
    tenant_id: Optional[str],
) -> OnboardingSubmissionOut:
    submission = await _find_submission_for_user(
        user_id=user_id, tenant_id=tenant_id, allow_completed=False
    )
    if submission.status != OnboardingStatus.PARTIAL_ACCEPTED:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="No outstanding onboarding fields to complete",
            details={"status": submission.status.value},
        )

    pending = set(submission.pending_field_keys)
    extra_keys = [k for k in payload.values.keys() if k not in pending]
    if extra_keys:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Submitted keys are not in the pending field list",
            details={"unexpected_keys": extra_keys},
        )
    missing_keys = [k for k in pending if k not in payload.values]
    if missing_keys:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing required pending fields",
            details={"missing_keys": missing_keys},
        )

    merged_payload = dict(submission.payload)
    merged_payload.update(payload.values)

    update = OnboardingSubmissionUpdate(
        payload=merged_payload,
        pending_field_keys=[],
        pending_field_labels={},
        status=OnboardingStatus.COMPLETED,
    )
    updated = await update_submission_by_id(submission.id or "", update)
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to record completion",
        )

    try:
        await record_audit_event(
            actor_id=user_id,
            actor_role="super_admin",
            action="onboarding.completed",
            resource_type="onboarding_submission",
            resource_id=submission.id or "",
            tenant_id=tenant_id,
            details={"completed_keys": sorted(payload.values.keys())},
        )
    except Exception:
        pass

    await _queue_status_email(updated, template_key="onboarding_completed")
    return updated


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _ensure_self_onboarding_enabled() -> None:
    from config.platform_config import get_platform_config

    if not get_platform_config().self_onboarding_enabled:
        raise AppException(
            status_code=403,
            code=ErrorCode.FEATURE_DISABLED,
            message="Self-onboarding is currently disabled",
            details={"flag": "self_onboarding_enabled"},
        )


async def _verify_turnstile_token(token: str, *, client_ip: Optional[str]) -> None:
    """Verify a Cloudflare Turnstile token.

    When ``TURNSTILE_SECRET_KEY`` is unset (development), verification is
    skipped — the endpoint still requires a non-empty token at the schema
    level so the frontend integration cannot accidentally drop the field.
    """
    settings = get_settings()
    secret = settings.turnstile_secret_key
    if not secret:
        logger.debug(
            "TURNSTILE_SECRET_KEY not configured; accepting submission "
            "without verification (env=%s)",
            settings.env,
        )
        return

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                settings.turnstile_verify_url,
                data={
                    "secret": secret,
                    "response": token,
                    "remoteip": client_ip or "",
                },
            )
            data = response.json()
    except Exception as exc:
        logger.warning("Turnstile verification HTTP failure: %s", exc)
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Security check failed",
            details={"error": "turnstile_unreachable"},
        )

    if not isinstance(data, dict) or not data.get("success"):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Security check failed",
            details={
                "error": "turnstile_failed",
                "codes": data.get("error-codes") if isinstance(data, dict) else None,
            },
        )


def _extract_indexed_fields(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Walk the alias lists in order; first non-empty string wins."""

    def _pick(keys: tuple[str, ...]) -> Optional[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    email = _pick(EMAIL_KEYS)
    return {
        "email": normalize_email(email) if email else None,
        "full_name": _pick(NAME_KEYS),
        "organization_name": _pick(ORGANIZATION_KEYS),
    }


def _ensure_in_terminal_window(
    submission: OnboardingSubmissionOut, action: str
) -> None:
    if submission.status in (
        OnboardingStatus.ACCEPTED,
        OnboardingStatus.PARTIAL_ACCEPTED,
        OnboardingStatus.COMPLETED,
    ):
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Cannot {action} a submission already accepted; offboard the tenant instead",
            details={"status": submission.status.value},
        )


async def _accept_internal(
    *,
    submission_id: str,
    payload: OnboardingAcceptRequest,
    actor_id: str,
    pending_field_keys: List[str],
    target_status: OnboardingStatus,
    email_template: str,
) -> OnboardingAcceptOut:
    from repositories.system_user_repo import get_system_user
    from schemas.system_user_schema import SystemUserCreate
    from services.system_user_service import add_system_user
    from services.tenant_service import add_tenant

    submission = await retrieve_onboarding_submission(submission_id)
    if submission.status != OnboardingStatus.NEW:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Only NEW submissions can be accepted",
            details={"status": submission.status.value},
        )

    company_name = (payload.company_name or submission.organization_name or "").strip()
    admin_full_name = (payload.admin_full_name or submission.full_name or "").strip()
    admin_email = (
        str(payload.admin_email) if payload.admin_email else (submission.email or "")
    )
    admin_email = admin_email.strip()

    missing: List[str] = []
    if not company_name:
        missing.append("company_name")
    if not admin_full_name:
        missing.append("admin_full_name")
    if not admin_email:
        missing.append("admin_email")
    if missing:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing fields required to provision the tenant",
            details={"missing": missing},
        )

    # The submission payload stores the email as a plain string, but the
    # system-user schema uses EmailStr — validate here so a bad address
    # (typo'd form value, reserved TLD, …) is a clear 400 instead of an
    # unhandled 500 halfway through provisioning.
    from pydantic import EmailStr as _EmailStr
    from pydantic import TypeAdapter as _TypeAdapter
    from pydantic import ValidationError as _ValidationError

    try:
        _TypeAdapter(_EmailStr).validate_python(admin_email)
    except _ValidationError as exc:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="admin_email is not a valid email address",
            details={
                "admin_email": admin_email,
                "errors": [err.get("msg") for err in exc.errors()],
            },
        )

    # Auto-generate a policy-compliant temporary password. The reviewing
    # admin no longer chooses it — we keep the raw value here only to
    # render the welcome email, which is the only channel that ever
    # sees the cleartext. The new super_admin row is marked
    # ``must_change_password=True`` so the gate dep refuses every
    # endpoint except ``POST /v1/auth/change-password`` until the user
    # picks their own. Test-domain accounts (non-production) get the
    # fixed test temp password instead — see core/test_mode.py.
    admin_password = issue_temp_password(admin_email)

    pending_field_labels: Dict[str, str] = {}
    if pending_field_keys:
        invalid = [k for k in pending_field_keys if k not in submission.field_labels]
        if invalid:
            raise AppException(
                status_code=400,
                code=ErrorCode.VALIDATION_FAILED,
                message="pending_field_keys must reference keys present in the submission",
                details={"invalid_keys": invalid},
            )
        pending_field_labels = {
            k: submission.field_labels[k] for k in pending_field_keys
        }

    tenant_create = TenantCreate(
        company_name=company_name,
        lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
        notice_display_mode=NoticeDisplayMode.PASSIVE,
    )
    tenant = await add_tenant(tenant_create)
    if not tenant.id:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Tenant provisioning returned no id",
        )

    existing_super = await get_system_user(
        {"tenant_id": tenant.id, "role": SystemUserRole.SUPER_ADMIN.value}
    )
    if existing_super:
        await delete_tenant({"_id": ObjectId(tenant.id)})
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Tenant already has a super admin",
        )

    try:
        super_admin = await add_system_user(
            SystemUserCreate(
                tenant_id=tenant.id,
                full_name=admin_full_name,
                email=admin_email,
                role=SystemUserRole.SUPER_ADMIN,
                account_status=AccountStatus.ACTIVE,
                password_hash=admin_password,
                is_main_super_admin=True,
                must_change_password=True,
                must_change_password_at=_temp_password_issued_at(),
            )
        )
    except Exception:
        await delete_tenant({"_id": ObjectId(tenant.id)})
        raise

    # Register the tenant as a Paystack customer up-front (everyone starts on
    # Free, so this is only a contact record — charging needs a card
    # authorization captured on the first real payment). Best-effort and only
    # when Paystack is configured; mirrors services.tenant_service.bootstrap_tenant.
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
                email=admin_email,
                name=company_name,
            )
    except Exception:
        logger.warning(
            "Paystack customer provisioning failed for tenant_id=%s",
            tenant.id,
            exc_info=True,
        )

    update = OnboardingSubmissionUpdate(
        status=target_status,
        tenant_id=tenant.id,
        super_admin_user_id=super_admin.id,
        pending_field_keys=pending_field_keys,
        pending_field_labels=pending_field_labels,
        review_notes=payload.review_notes,
        reviewed_by=actor_id,
        reviewed_at=int(time.time()),
    )
    updated = await update_submission_by_id(submission_id, update)
    if updated is None:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to update onboarding submission after provisioning",
        )

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="admin",
            action=f"onboarding.{target_status.value}",
            resource_type="onboarding_submission",
            resource_id=submission_id,
            tenant_id=tenant.id,
            details={
                "tenant_id": tenant.id,
                "super_admin_user_id": super_admin.id,
                "pending_field_keys": pending_field_keys,
            },
        )
    except Exception:
        pass

    await _queue_status_email(
        updated,
        template_key=email_template,
        review_notes=payload.review_notes,
        admin_email=admin_email,
        temp_password=admin_password,
    )

    return OnboardingAcceptOut(
        submission_id=submission_id,
        status=target_status,
        tenant_id=tenant.id,
        super_admin_user_id=super_admin.id or "",
        pending_field_keys=pending_field_keys,
    )


async def _find_submission_for_user(
    *,
    user_id: str,
    tenant_id: Optional[str],
    allow_completed: bool,
) -> OnboardingSubmissionOut:
    """Locate the submission tied to the calling super_admin.

    Lookup is by ``super_admin_user_id`` first (most precise), then by
    ``tenant_id`` as a fallback. Either condition is enough — we don't want
    to leak data about other tenants' submissions, so a missing match returns
    404 rather than scanning the whole collection.
    """
    from repositories.onboarding_submission_repo import (
        get_submission as _get_submission,
    )

    filter_dict: Dict[str, Any] = {"super_admin_user_id": user_id}
    submission = await _get_submission(filter_dict)
    if submission is None and tenant_id:
        submission = await _get_submission({"tenant_id": tenant_id})
    if submission is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="No onboarding submission found for this account",
        )
    if not allow_completed and submission.status == OnboardingStatus.COMPLETED:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Onboarding has already been completed",
            details={"status": submission.status.value},
        )
    return submission


async def _queue_status_email(
    submission: OnboardingSubmissionOut,
    *,
    template_key: str,
    review_notes: Optional[str] = None,
    admin_email: Optional[str] = None,
    temp_password: Optional[str] = None,
) -> None:
    """Queue a templated status email — silent on failure.

    The template files live alongside other email templates; if they're not
    yet mounted the EmailManager logs and drops the message rather than
    blocking onboarding state changes.

    ``admin_email`` / ``temp_password`` are populated by the accept paths so
    the welcome email can carry sign-in credentials. They are NOT persisted
    anywhere — the only place a cleartext password ever appears is the
    rendered email payload that goes out to the new super_admin.
    """
    if not submission.email:
        return
    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest
        from core.settings import get_settings

        settings = get_settings()
        platform_name = settings.email_sender_name or "VisiChek"
        login_url = (settings.app_base_url or "").rstrip("/")
        if login_url:
            # New tenant super_admins sign in at the tenant portal.
            login_url = f"{login_url}/app/login"

        context: Dict[str, Any] = {
            "full_name": submission.full_name or "",
            "organization_name": submission.organization_name or "",
            "review_notes": review_notes or submission.review_notes or "",
            "pending_field_keys": list(submission.pending_field_keys),
            "pending_field_labels": dict(submission.pending_field_labels),
            "tenant_id": submission.tenant_id or "",
            "platform_name": platform_name,
            "login_url": login_url,
            "admin_email": admin_email or submission.email or "",
            "temp_password": temp_password or "",
        }
        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=submission.email,
                template_key=template_key,
                context=context,
                dispatch="queued",
            )
        )
    except Exception:
        logger.warning(
            "onboarding email dispatch failed template=%s submission=%s",
            template_key,
            submission.id,
            exc_info=True,
        )
