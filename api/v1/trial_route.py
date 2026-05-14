"""Per-tenant trial code endpoints.

* ``POST /v1/trials/claim``    — claim (or re-fetch) the tenant's
  one-time trial code for a trial-supporting plan. Returns the code +
  plan preview so the FE can render a trial summary card. Idempotent:
  re-calling for the same plan returns the same pending code.
* ``GET  /v1/trials/preview``  — read-only validate + summarise a
  trial code for display (no mutation). Mirrors the discount preview
  contract so the FE can run one shape against both code types.
* ``GET  /v1/trials/me``       — quick status lookup ("does this
  tenant have an outstanding pending trial?"). Returns either the
  pending row or ``null``.

Tenant super_admin only. The actual redemption lives in the checkout
flow: pass the ``code`` as ``trial_code`` on ``POST /v1/checkout/sessions``
and the trial only marks ``USED`` once the $0 checkout completes.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, status

from core.errors import auth_permission_denied
from core.response_envelope import document_response
from repositories.trial_code_repo import get_tenant_pending_trial
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.trial_code_service import (
    claim_trial_for_tenant,
    preview_trial_code,
)

router = APIRouter(prefix="/trials", tags=["Trials"])


def _require_tenant_scope(principal: AuthPrincipal) -> str:
    tenant_id = getattr(principal, "tenant_id", None)
    if not tenant_id:
        raise auth_permission_denied(permission_key="trials.manage")
    return tenant_id


@router.post("/claim")
@document_response(
    message="Trial code retrieved",
    status_code=status.HTTP_200_OK,
    description=(
        "Claim (or re-fetch) the tenant's one-time trial code for the "
        "requested plan. Returns 400 TRIAL_NOT_SUPPORTED if the plan does "
        "not offer a trial, 409 TRIAL_ALREADY_USED if the tenant has "
        "ever redeemed a trial before. Tenant super_admin only."
    ),
    summary="Claim trial code",
)
async def claim_trial_endpoint(
    plan_id: str = Query(..., description="Plan to start a trial for"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = _require_tenant_scope(principal)
    return await claim_trial_for_tenant(
        tenant_id=tenant_id,
        plan_id=plan_id,
        actor_id=principal.user_id,
    )


@router.get("/preview")
@document_response(
    message="Trial code preview retrieved",
    description=(
        "Validate a trial code and return the applied-trial summary. "
        "Read-only — does not consume the code. Use this to render the "
        "trial summary before posting to /v1/checkout/sessions."
    ),
    summary="Preview trial code",
)
async def preview_trial_endpoint(
    code: str = Query(..., description="Trial code returned by /trials/claim"),
    plan_id: str = Query(..., description="Plan the trial will apply to"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = _require_tenant_scope(principal)
    return await preview_trial_code(
        code=code,
        tenant_id=tenant_id,
        plan_id=plan_id,
    )


@router.get("/me")
@document_response(
    message="Trial status retrieved",
    description=(
        "Return the tenant's outstanding pending trial, or ``null`` if "
        "none. Use this on the billing page mount to decide whether to "
        "show the trial CTA. Tenant super_admin only."
    ),
    summary="Get my pending trial",
)
async def get_my_pending_trial(
    plan_id: Optional[str] = Query(
        None, description="Restrict to a pending trial on this plan"
    ),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = _require_tenant_scope(principal)
    return await get_tenant_pending_trial(tenant_id, plan_id=plan_id)
