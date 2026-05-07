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
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.onboarding_submission_service import (
    complete_onboarding_for_user,
    get_pending_fields_for_user,
    submit_onboarding,
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
