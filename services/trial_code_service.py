"""Per-tenant one-time trial code service.

Owns the lifecycle for a tenant claiming, previewing, and redeeming a
trial code:

* ``claim_trial_for_tenant`` — returns the tenant's outstanding
  pending code for the requested plan, generating a fresh one if
  necessary. Rejects when the plan does not offer a trial, when the
  tenant has already redeemed any trial, or when the plan is not
  active.
* ``preview_trial_code`` — given a code + plan, returns the
  applied-trial summary (length, simulated ``trial_ends_at``, $0 price)
  without mutating anything. The frontend uses this to render the
  trial page before submitting to checkout.
* ``validate_trial_code_for_checkout`` — strict check used by the
  checkout-create path before issuing a $0 session.
* ``mark_trial_code_used`` — invoked from the checkout-completion
  path once the $0 payment has actually cleared. Idempotent.
* ``mark_trial_code_cancelled`` — called when a checkout that was
  holding a trial is cancelled before completion, so the same code can
  be reused on a fresh checkout.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

from bson import ObjectId
from fastapi import status
from pymongo.errors import DuplicateKeyError

from core.errors import AppException, ErrorCode, resource_not_found
from repositories.plan_repo import get_plan
from repositories.trial_code_repo import (
    create_trial_code,
    get_tenant_pending_trial,
    get_tenant_redeemed_trial,
    get_trial_code,
    update_trial_code,
)
from schemas.plan_schema import PlanOut, PlanStatus
from schemas.trial_code_schema import (
    TrialClaimResponse,
    TrialCodeCreate,
    TrialCodeOut,
    TrialCodeStatus,
    TrialCodeUpdate,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_trial_code(tenant_id: str) -> str:
    """Build an opaque-but-debuggable trial code.

    Format: ``TRIAL-<tenant-suffix>-<8 hex>``. The tenant suffix gives
    support staff a quick visual link back to a tenant without having to
    cross-reference the trial_codes collection.
    """
    tenant_suffix = (tenant_id or "")[-6:].upper() if tenant_id else "ANON"
    return f"TRIAL-{tenant_suffix}-{secrets.token_hex(4).upper()}"


async def _load_active_plan(plan_id: str) -> PlanOut:
    if not ObjectId.is_valid(plan_id):
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid plan_id",
            details={"plan_id": plan_id},
        )
    plan = await get_plan({"_id": ObjectId(plan_id)})
    if not plan:
        raise resource_not_found(resource="Plan", resource_id=plan_id)
    if plan.status != PlanStatus.ACTIVE:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.VALIDATION_FAILED,
            message="Plan is not active",
            details={"plan_id": plan_id, "status": plan.status.value},
        )
    return plan


def _build_claim_response(code: TrialCodeOut, plan: PlanOut) -> TrialClaimResponse:
    now = int(time.time())
    return TrialClaimResponse(
        code=code.code,
        plan_id=code.plan_id,
        plan_name=plan.name,
        plan_display_name=plan.display_name,
        trial_days=code.trial_days_snapshot,
        trial_ends_at_preview=now + (code.trial_days_snapshot * 86400),
        base_price_monthly=plan.base_price_monthly,
        base_price_yearly=plan.base_price_yearly,
        currency=plan.currency,
        status=code.status,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def claim_trial_for_tenant(
    *,
    tenant_id: str,
    plan_id: str,
    actor_id: Optional[str] = None,
) -> TrialClaimResponse:
    """Return the tenant's pending trial code for the requested plan.

    Behaviour:
    * If the tenant has *ever* successfully used a trial → 409
      ``TRIAL_ALREADY_USED``.
    * If the plan does not support trials (``trial_days <= 0``) → 400
      ``TRIAL_NOT_SUPPORTED``.
    * If the tenant has an outstanding *pending* code for this same
      plan, return it (idempotent — the tenant can hit claim repeatedly
      from the UI without burning codes).
    * If the tenant has a pending code for a *different* plan, cancel
      it (so trials don't lock the tenant into one plan) and mint a
      fresh one.
    * Otherwise, generate a brand-new code.
    """
    plan = await _load_active_plan(plan_id)
    if plan.trial_days <= 0:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.TRIAL_NOT_SUPPORTED,
            message="This plan does not offer a trial",
            details={"plan_id": plan_id, "trial_days": plan.trial_days},
        )

    redeemed = await get_tenant_redeemed_trial(tenant_id)
    if redeemed:
        raise AppException(
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.TRIAL_ALREADY_USED,
            message="Organization has already redeemed their trial",
            details={
                "tenant_id": tenant_id,
                "redeemed_trial_id": redeemed.id,
                "redeemed_plan_id": redeemed.plan_id,
            },
        )

    existing_pending_same_plan = await get_tenant_pending_trial(
        tenant_id, plan_id=plan_id
    )
    if existing_pending_same_plan:
        return _build_claim_response(existing_pending_same_plan, plan)

    existing_pending_other_plan = await get_tenant_pending_trial(tenant_id)
    if existing_pending_other_plan and existing_pending_other_plan.id:
        await update_trial_code(
            {"_id": ObjectId(existing_pending_other_plan.id)},
            TrialCodeUpdate(status=TrialCodeStatus.CANCELLED),
        )

    code_str = _generate_trial_code(tenant_id)
    created = await create_trial_code(
        TrialCodeCreate(
            code=code_str,
            tenant_id=tenant_id,
            plan_id=plan_id,
            trial_days_snapshot=plan.trial_days,
            status=TrialCodeStatus.PENDING,
        )
    )

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role="super_admin",
            action="trial.claimed",
            resource_type="trial_code",
            resource_id=str(created.id),
            tenant_id=tenant_id,
            details={
                "plan_id": plan_id,
                "trial_days": plan.trial_days,
                "code": code_str,
            },
        )
    except Exception:
        pass

    return _build_claim_response(created, plan)


async def preview_trial_code(
    *,
    code: str,
    tenant_id: str,
    plan_id: str,
) -> TrialClaimResponse:
    """Validate a trial code for display + return the applied trial summary.

    Read-only — does NOT mutate anything. Mirrors the discount preview
    contract so the FE can use one shape.
    """
    trial = await get_trial_code({"code": code})
    if not trial:
        raise resource_not_found(resource="Trial code", resource_id=code)
    if trial.tenant_id != tenant_id:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code does not belong to this organization",
            details={"code": code, "tenant_id": tenant_id},
        )
    if trial.status != TrialCodeStatus.PENDING:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code is not redeemable",
            details={"code": code, "status": trial.status.value},
        )
    if trial.plan_id != plan_id:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code does not apply to this plan",
            details={"code": code, "plan_id": plan_id, "trial_plan_id": trial.plan_id},
        )

    plan = await _load_active_plan(plan_id)
    return _build_claim_response(trial, plan)


async def validate_trial_code_for_checkout(
    *,
    code: str,
    tenant_id: str,
    plan_id: str,
) -> TrialCodeOut:
    """Strict pre-flight check used by the checkout-create path.

    Same rules as ``preview_trial_code`` but returns the raw trial row so
    the checkout service can snapshot ``trial_days_snapshot`` onto the
    session.
    """
    trial = await get_trial_code({"code": code})
    if not trial:
        raise resource_not_found(resource="Trial code", resource_id=code)
    if trial.tenant_id != tenant_id:
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code does not belong to this organization",
            details={"code": code, "tenant_id": tenant_id},
        )
    if trial.status != TrialCodeStatus.PENDING:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code is not redeemable",
            details={"code": code, "status": trial.status.value},
        )
    if trial.plan_id != plan_id:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.TRIAL_INVALID,
            message="Trial code does not apply to this plan",
            details={"code": code, "plan_id": plan_id, "trial_plan_id": trial.plan_id},
        )
    redeemed = await get_tenant_redeemed_trial(tenant_id)
    if redeemed and redeemed.id != trial.id:
        raise AppException(
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.TRIAL_ALREADY_USED,
            message="Organization has already redeemed their trial",
            details={"tenant_id": tenant_id, "redeemed_trial_id": redeemed.id},
        )
    return trial


async def mark_trial_code_used(
    *,
    code: str,
    subscription_id: str,
    tenant_id: str,
) -> Optional[TrialCodeOut]:
    """Mark a trial code as redeemed. Called from the checkout-success path.

    Idempotent — re-running with an already-USED row returns the row
    unchanged. Returns ``None`` if no matching row exists (caller can
    log; we never want to fail subscription provisioning on this).
    """
    trial = await get_trial_code({"code": code, "tenant_id": tenant_id})
    if not trial or not trial.id:
        return None
    if trial.status == TrialCodeStatus.USED:
        return trial
    try:
        updated = await update_trial_code(
            {"_id": ObjectId(trial.id)},
            TrialCodeUpdate(
                status=TrialCodeStatus.USED,
                subscription_id=subscription_id,
                used_at=int(time.time()),
            ),
        )
    except DuplicateKeyError:
        # The tenant_used_trial_unique partial-unique index fired: another
        # redemption already won the race and marked a different code USED
        # for this tenant. One-time-trial invariant holds — never fail
        # subscription provisioning on this. Return the winning row.
        logger.warning(
            "trial.redeem race: tenant=%s already has a redeemed trial; "
            "leaving code=%s pending (DuplicateKeyError on tenant_used_trial_unique)",
            tenant_id,
            code,
        )
        return await get_tenant_redeemed_trial(tenant_id)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="system",
            action="trial.redeemed",
            resource_type="trial_code",
            resource_id=str(trial.id),
            tenant_id=tenant_id,
            details={"subscription_id": subscription_id, "code": code},
        )
    except Exception:
        pass
    return updated


async def mark_trial_code_cancelled(
    *,
    code: str,
    tenant_id: str,
) -> Optional[TrialCodeOut]:
    """Release a held trial code so the tenant can claim again."""
    trial = await get_trial_code({"code": code, "tenant_id": tenant_id})
    if not trial or not trial.id:
        return None
    if trial.status != TrialCodeStatus.PENDING:
        return trial
    return await update_trial_code(
        {"_id": ObjectId(trial.id)},
        TrialCodeUpdate(status=TrialCodeStatus.CANCELLED),
    )
