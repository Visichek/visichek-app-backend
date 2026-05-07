"""Application-admin management of self-onboarding submissions.

These endpoints sit under ``/v1/tenants/onboarding`` so they live on the
tenant page in the admin portal next to the rest of the tenant-management
surface. Only application admins (role=admin) can call them.

Available actions on a submission:

* ``GET     /v1/tenants/onboarding``                — list (filterable by status)
* ``GET     /v1/tenants/onboarding/{id}``           — read
* ``POST    /v1/tenants/onboarding/{id}/accept``    — provision tenant + super_admin
* ``POST    /v1/tenants/onboarding/{id}/partial-accept`` — same, plus pending fields
* ``POST    /v1/tenants/onboarding/{id}/reject``    — terminate with notes
* ``POST    /v1/tenants/onboarding/{id}/archive``   — hide from default list
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from schemas.imports import OnboardingStatus
from schemas.onboarding_submission_schema import (
    OnboardingAcceptOut,
    OnboardingAcceptRequest,
    OnboardingPartialAcceptRequest,
    OnboardingRejectRequest,
    OnboardingSubmissionOut,
)
from security.account_status_check import check_admin_account_status_and_permissions
from services.onboarding_submission_service import (
    accept_onboarding_submission,
    archive_onboarding_submission,
    list_onboarding_submissions,
    partial_accept_onboarding_submission,
    reject_onboarding_submission,
    retrieve_onboarding_submission,
)

router = APIRouter(prefix="/tenants/onboarding", tags=["Tenant Onboarding"])


@router.get("")
@document_response(
    message="Onboarding submissions fetched successfully",
    description=(
        "List self-onboarding submissions. Filter with ``status`` to focus on "
        "new (default work queue) or other lifecycle states."
    ),
    summary="List onboarding submissions",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_submissions_endpoint(
    status: Annotated[
        Optional[OnboardingStatus],
        Query(description="Filter by submission status"),
    ] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    items, total = await list_onboarding_submissions(
        status=status, skip=skip, limit=limit
    )
    return {
        "items": [item.model_dump(mode="json", by_alias=True) for item in items],
        "total": total,
    }


@router.get("/{submission_id}")
@document_response(
    message="Onboarding submission fetched successfully",
    description="Retrieve a single onboarding submission by id.",
    summary="Get onboarding submission",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Submission not found",
    },
)
async def get_submission_endpoint(
    submission_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OnboardingSubmissionOut:
    return await retrieve_onboarding_submission(submission_id)


@router.post("/{submission_id}/accept")
@document_response(
    message="Onboarding submission accepted",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Provision a tenant + first super_admin from the submission. "
        "Defaults company_name / admin_full_name / admin_email from the "
        "submission's extracted fields; pass values in the body to override."
    ),
    summary="Accept onboarding submission (full)",
    response_codes={
        400: "Missing required fields",
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Submission not found",
        409: "Submission already processed",
    },
)
async def accept_submission_endpoint(
    submission_id: str,
    payload: OnboardingAcceptRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OnboardingAcceptOut:
    return await accept_onboarding_submission(
        submission_id=submission_id,
        payload=payload,
        actor_id=admin.id or "",  # type: ignore[arg-type]
    )


@router.post("/{submission_id}/partial-accept")
@document_response(
    message="Onboarding submission partially accepted",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Same as accept, but flag a list of payload keys whose values were "
        "missing or unsatisfactory. The newly provisioned super_admin must "
        "fill these in via /v1/onboarding/me/complete before the tenant is "
        "considered fully onboarded."
    ),
    summary="Accept onboarding submission (partial)",
    response_codes={
        400: "Missing required fields or invalid pending keys",
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Submission not found",
        409: "Submission already processed",
    },
)
async def partial_accept_submission_endpoint(
    submission_id: str,
    payload: OnboardingPartialAcceptRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OnboardingAcceptOut:
    return await partial_accept_onboarding_submission(
        submission_id=submission_id,
        payload=payload,
        actor_id=admin.id or "",  # type: ignore[arg-type]
    )


@router.post("/{submission_id}/reject")
@document_response(
    message="Onboarding submission rejected",
    description=(
        "Mark a submission as rejected with a required note. The note is "
        "passed to the rejection email template if email is configured."
    ),
    summary="Reject onboarding submission",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Submission not found",
        409: "Submission already accepted",
    },
)
async def reject_submission_endpoint(
    submission_id: str,
    payload: OnboardingRejectRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OnboardingSubmissionOut:
    return await reject_onboarding_submission(
        submission_id=submission_id,
        payload=payload,
        actor_id=admin.id or "",  # type: ignore[arg-type]
    )


@router.post("/{submission_id}/archive")
@document_response(
    message="Onboarding submission archived",
    description=(
        "Hide a submission from default listings. Useful for spam / "
        "duplicates that don't warrant a rejection email."
    ),
    summary="Archive onboarding submission",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Submission not found",
    },
)
async def archive_submission_endpoint(
    submission_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OnboardingSubmissionOut:
    return await archive_onboarding_submission(
        submission_id=submission_id,
        actor_id=admin.id or "",  # type: ignore[arg-type]
    )
