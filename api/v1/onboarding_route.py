"""Public + tenant-self-completion onboarding endpoints.

Two surfaces live here:

* Public ``POST /v1/onboarding/submissions`` — schema-on-read lead capture from
  the marketing site. Gated by ``platform_settings.self_onboarding_enabled``;
  the route stays mounted regardless of the flag and returns 403
  ``FEATURE_DISABLED`` when off.
* Tenant-side ``GET /v1/onboarding/me/pending-fields`` and
  ``POST /v1/onboarding/me/complete`` — used by a super_admin whose tenant
  was provisioned via partial-accept to finish supplying the fields the
  application admin flagged. These require a super_admin token.

Application-admin management of submissions lives in
``api/v1/admin_onboarding_route.py`` (mounted under ``/v1/tenants``) so it
sits next to the rest of the tenant-page endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from core.response_envelope import document_response
from schemas.onboarding_submission_schema import (
    OnboardingCompleteRequest,
    OnboardingPendingFieldsOut,
    OnboardingSubmissionOut,
    OnboardingSubmissionRequest,
)
from schemas.tenant_schema import (
    TenantInfoConfirmRequest,
    TenantInfoConfirmationOut,
)
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.onboarding_submission_service import (
    complete_onboarding_for_user,
    get_pending_fields_for_user,
    submit_onboarding,
)
from services.tenant_service import (
    confirm_tenant_info,
    get_tenant_info_confirmation,
)

router = APIRouter(prefix="/onboarding", tags=["Self-Onboarding"])


@router.post("/submissions")
@document_response(
    message="Submission received",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public lead-capture endpoint for the marketing site. Stores the form "
        "submission verbatim alongside the field labels and order so old rows "
        "render correctly even after the form is redesigned. Cloudflare "
        "Turnstile is verified server-side when TURNSTILE_SECRET_KEY is set. "
        "Returns 403 FEATURE_DISABLED when "
        "platform_settings.self_onboarding_enabled is false — the endpoint "
        "itself stays mounted so the frontend can detect the disabled state."
    ),
    summary="Submit a self-onboarding lead",
    success_example={"submission_id": "64f1a2b3c4d5e6f7a8b9c0d1"},
    response_codes={
        400: "Validation or Turnstile failure",
        403: "Self-onboarding currently disabled",
        429: "Rate limited",
    },
)
async def submit_onboarding_endpoint(
    submission: OnboardingSubmissionRequest,
    request: Request,
):
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    user_agent = request.headers.get("User-Agent")
    result = await submit_onboarding(
        request=submission,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return {"submission_id": result.id}


@router.get("/me/pending-fields")
@document_response(
    message="Pending onboarding fields fetched successfully",
    description=(
        "Returns the list of field keys + human-readable labels the calling "
        "super_admin still owes the application admin. Used by the in-app "
        "completion screen after a partial acceptance."
    ),
    summary="Get my pending onboarding fields",
    response_codes={
        401: "Unauthorized",
        404: "No onboarding submission found for this account",
    },
)
async def get_my_pending_fields(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> OnboardingPendingFieldsOut:
    return await get_pending_fields_for_user(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )


@router.post("/me/complete")
@document_response(
    message="Onboarding completion submitted successfully",
    description=(
        "Submit values for the field keys returned by /me/pending-fields. "
        "Server validates that the submitted keys exactly match the pending "
        "set — extras are rejected, missing values are rejected. On success "
        "the submission's status flips to COMPLETED and the values are merged "
        "into the original payload."
    ),
    summary="Complete my onboarding",
    response_codes={
        400: "Submitted keys do not match pending set",
        401: "Unauthorized",
        404: "No onboarding submission found for this account",
        409: "Submission is not in partial_accepted state",
    },
)
async def complete_my_onboarding(
    payload: OnboardingCompleteRequest,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> OnboardingSubmissionOut:
    return await complete_onboarding_for_user(
        payload,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )


@router.get("/me/tenant-confirmation")
@document_response(
    message="Tenant info fetched for confirmation",
    description=(
        "Returns the company details carried over from onboarding for the "
        "calling super_admin to review on first login: company name, DPO "
        "contact email, privacy policy URL, country of hosting, plus the "
        "confirmation status. When the tenant was provisioned from a "
        "self-onboarding submission, the original form values + labels + "
        "order are attached under onboarding_fields / onboarding_field_labels "
        "/ onboarding_field_order so the UI can show 'this is what you told "
        "us'. This is a soft prompt — the API does NOT block other calls when "
        "onboarding_info_confirmed is false; the frontend decides when to show "
        "the review screen."
    ),
    summary="Get my tenant info for first-login confirmation",
    response_codes={
        401: "Unauthorized",
        403: "Not a super_admin",
        404: "Tenant not found",
    },
)
async def get_my_tenant_confirmation(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TenantInfoConfirmationOut:
    return await get_tenant_info_confirmation(tenant_id=principal.tenant_id or "")


@router.post("/me/tenant-confirmation")
@document_response(
    message="Tenant info confirmed",
    description=(
        "Confirm — and optionally correct — the company details for the "
        "calling super_admin's tenant. Every field in the body is optional; "
        "omitting one keeps the current value. Submitting the request (with "
        "or without edits) sets onboarding_info_confirmed=true and stamps "
        "onboarding_info_confirmed_at. Any edits are applied to the tenant "
        "record and audited with a field-level diff. Returns the refreshed "
        "confirmation payload."
    ),
    summary="Confirm my tenant info (first login)",
    response_codes={
        401: "Unauthorized",
        403: "Not a super_admin",
        404: "Tenant not found",
        422: "Validation error",
    },
)
async def confirm_my_tenant_info(
    payload: TenantInfoConfirmRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TenantInfoConfirmationOut:
    return await confirm_tenant_info(
        payload,
        tenant_id=principal.tenant_id or "",
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
