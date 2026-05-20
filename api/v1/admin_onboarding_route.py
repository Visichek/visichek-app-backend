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

from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, coerce_bool, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from schemas.onboarding_submission_schema import (
    OnboardingAcceptOut,
    OnboardingAcceptRequest,
    OnboardingPartialAcceptRequest,
    OnboardingRejectRequest,
    OnboardingSubmissionOut,
)
from security.account_status_check import check_admin_account_status_and_permissions
from schemas.onboarding_submission_schema import MarketingOptInEmailsOut
from services.onboarding_submission_service import (
    accept_onboarding_submission,
    archive_onboarding_submission,
    list_marketing_opt_in_emails,
    partial_accept_onboarding_submission,
    reject_onboarding_submission,
    retrieve_onboarding_submission,
)

router = APIRouter(prefix="/tenants/onboarding", tags=["Tenant Onboarding"])


_ONBOARDING_STATUSES = frozenset(
    {"new", "partial_accepted", "completed", "accepted", "rejected", "archived"}
)


ONBOARDING_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"submitted_at", "status", "organization_name", "date_created"}
    ),
    default_sort=(("submitted_at", -1),),
    search_fields=("organization_name", "full_name", "email"),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=_ONBOARDING_STATUSES,
        ),
        "turnstileVerified": FilterDef(
            name="turnstileVerified",
            mongo_field="turnstile_verified",
            coerce=coerce_bool,
        ),
    },
    range_filters={"submittedAt": "submitted_at"},
    facet_fields=frozenset({"status"}),
)


def _map_onboarding_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _onboarding_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _ONBOARDING_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


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
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    query = parse_list_query(request, ONBOARDING_LIST_SPEC)
    return await run_list(
        collection=db.onboarding_submissions,
        query=query,
        map_doc=_map_onboarding_doc,
        facet_runner=_onboarding_status_facet,
    )


@router.get("/marketing-opt-ins")
@document_response(
    message="Marketing opt-in emails fetched successfully",
    description=(
        "Returns the deduplicated, sorted list of normalized work emails for "
        "every onboarding submission whose ``marketing_opt_in`` field is set "
        "to an affirmative value. Use this to compose marketing campaign "
        "recipient lists. Emails are stored normalized (lowercased, "
        "+aliases stripped, gmail dots removed) so the list is duplicate-free "
        "across re-submissions from the same person. Submissions with no "
        "extracted email are skipped."
    ),
    summary="List marketing opt-in emails",
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_marketing_opt_in_emails_endpoint(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> MarketingOptInEmailsOut:
    emails, total = await list_marketing_opt_in_emails()
    return MarketingOptInEmailsOut(emails=emails, total=total)


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
) -> Any:
    return await get_or_compute_entity(
        entity_type="onboarding_submission",
        entity_id=submission_id,
        loader=lambda: retrieve_onboarding_submission(submission_id),
    )


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


@router.post("/bulk/archive")
@document_response(
    message="Bulk onboarding archive queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk archive onboarding submissions",
)
async def bulk_archive_submissions(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    actor_id = admin.id or ""
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/onboarding/bulk/archive",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="onboarding.bulk_archive",
        ids=payload.get("ids", []),
        resource_type="onboarding_submission",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/onboarding/bulk/archive",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/reject")
@document_response(
    message="Bulk onboarding reject queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk reject onboarding submissions",
)
async def bulk_reject_submissions(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    actor_id = admin.id or ""
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/onboarding/bulk/reject",
        body=payload,
    )
    if hit is not None:
        return hit.response
    notes = str(payload.get("notes") or "")[:1000]
    response = await enqueue_bulk_write(
        writer_key="onboarding.bulk_reject",
        ids=payload.get("ids", []),
        resource_type="onboarding_submission",
        extras={"notes": notes},
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/onboarding/bulk/reject",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


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
