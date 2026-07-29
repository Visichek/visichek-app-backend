from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Query, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.errors import auth_permission_denied, auth_role_mismatch
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import (
    FilterDef,
    ListSpec,
    coerce_bool,
    parse_list_query,
)
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.tenant_schema import TenantCreate, TenantUpdate
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import (
    verify_any_token,
    verify_super_admin_token,
    verify_system_user_token,
)
from security.principal import AuthPrincipal
from services.tenant_service import (
    retrieve_tenant_by_id_with_summary,
    retrieve_tenants_with_summary,
)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


# ─── List spec ────────────────────────────────────────────────────────


def _status_filter_builder(values):
    """Translate ``status=active|inactive|all`` into ``is_active`` predicate."""
    if "all" in values:
        return {}
    bools: list[bool] = []
    for v in values:
        if v == "active":
            bools.append(True)
        elif v == "inactive":
            bools.append(False)
    if not bools:
        return {}
    if len(bools) == 1:
        return {"is_active": bools[0]}
    return {"is_active": {"$in": bools}}


_PLAN_TIERS = frozenset(
    {"free", "starter", "professional", "enterprise", "custom", "none"}
)
_SUB_STATUSES = frozenset(
    {"active", "trialing", "past_due", "cancelled", "suspended", "expired", "none"}
)
_LAWFUL_BASES = frozenset({"consent", "legitimate_interest"})


def _optional_bool_builder(mongo_field: str):
    """Build a predicate for an optional boolean tenant flag.

    These flags (``onboarding_info_confirmed``, ``dpa_accepted``,
    ``cross_border_approved``) were added after tenants already existed,
    so older documents simply omit them. A missing field must read as
    ``False`` for filtering: ``true`` matches only a stored ``True``,
    while ``false`` matches a stored ``False`` *or* an absent field via
    ``$ne: True``. Selecting both values imposes no constraint.
    """

    def _build(values):
        wants_true = "true" in values
        wants_false = "false" in values
        if wants_true and wants_false:
            return {}
        if wants_true:
            return {mongo_field: True}
        return {mongo_field: {"$ne": True}}

    return _build


TENANTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        # ``subscription_status`` lives on the read-time-computed plan_summary,
        # not the tenant document — sorting on it produced arbitrary order.
        # (The subscriptionStatus FILTER below is fine: it joins via the
        # subscriptions collection into base_filter before run_list.)
        {"company_name", "date_created", "is_active"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("company_name", "dpo_contact_email"),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=frozenset({"active", "inactive", "all"}),
            builder=_status_filter_builder,
        ),
        "planTier": FilterDef(name="planTier", multi=True, allowed_values=_PLAN_TIERS),
        "subscriptionStatus": FilterDef(
            name="subscriptionStatus", multi=True, allowed_values=_SUB_STATUSES
        ),
        "isActive": FilterDef(
            name="isActive", coerce=coerce_bool, mongo_field="is_active"
        ),
        "onboardingInfoConfirmed": FilterDef(
            name="onboardingInfoConfirmed",
            multi=True,
            allowed_values=frozenset({"true", "false"}),
            builder=_optional_bool_builder("onboarding_info_confirmed"),
        ),
        "dpaAccepted": FilterDef(
            name="dpaAccepted",
            multi=True,
            allowed_values=frozenset({"true", "false"}),
            builder=_optional_bool_builder("dpa_accepted"),
        ),
        "crossBorderApproved": FilterDef(
            name="crossBorderApproved",
            multi=True,
            allowed_values=frozenset({"true", "false"}),
            builder=_optional_bool_builder("cross_border_approved"),
        ),
        "lawfulBasis": FilterDef(
            name="lawfulBasis",
            multi=True,
            allowed_values=_LAWFUL_BASES,
            mongo_field="lawful_basis",
        ),
    },
    range_filters={"createdAt": "date_created"},
    facet_fields=frozenset({"status"}),
)


def _is_default_listing(request: Request) -> bool:
    """The fast-path precompute is only valid for an unfiltered first page.

    Anything that mutates the result set (filters, sort, q, facets,
    pagination cursor) bypasses the precomputed payload and goes
    straight to the live query so callers get a current count and the
    correct subset.
    """
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(TENANTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(TENANTS_LIST_SPEC.default_limit)


# ─── Service helpers (filter joins + map_doc) ─────────────────────────


async def _resolve_subscription_tenant_ids(
    *, plan_tiers: list[str], sub_statuses: list[str]
) -> Optional[list[str]]:
    """Pre-query subscriptions to translate plan/subscription filters to
    a set of tenant_ids the route can intersect with the tenant filter.

    Returns ``None`` if no subscription-side filter was requested.
    Returns ``[]`` (empty) when filters were provided but matched no
    subscription — the caller must short-circuit so it doesn't return
    *all* tenants.
    """
    if not plan_tiers and not sub_statuses:
        return None
    sub_filter: dict[str, Any] = {}
    want_no_sub = False
    if sub_statuses:
        statuses = [s for s in sub_statuses if s != "none"]
        want_no_sub = "none" in sub_statuses
        if statuses:
            sub_filter["status"] = {"$in": statuses}
    plan_ids: list[str] = []
    if plan_tiers:
        tiers = [t for t in plan_tiers if t != "none"]
        if tiers:
            plan_filter = {"tier": {"$in": tiers}}
            async for plan_doc in db.plans.find(plan_filter, {"_id": 1}):
                plan_ids.append(str(plan_doc["_id"]))
            if not plan_ids:
                # Tier filter has no matching plans → no subs match.
                return [] if not want_no_sub else None
            sub_filter["plan_id"] = {"$in": plan_ids}
    matched: list[str] = []
    cursor = db.subscriptions.find(sub_filter, {"tenant_id": 1})
    async for doc in cursor:
        tid = doc.get("tenant_id")
        if tid:
            matched.append(str(tid))
    if want_no_sub:
        # "none" is handled at the route by inverting the membership —
        # we return an empty list with a sentinel handled upstream.
        return matched  # caller handles inversion
    return matched


def _q_builds_id_match(q: Optional[str]) -> Optional[dict[str, Any]]:
    """If the q string looks like a hex24 ObjectId, allow id-based match."""
    if q and ObjectId.is_valid(q):
        return {"_id": ObjectId(q)}
    return None


def _map_tenant_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


# ─── Routes ───────────────────────────────────────────────────────────


@router.post("")
@document_response(
    message="Tenant creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a plain tenant create. For bootstrap (tenant + first "
        "super_admin atomically), use POST /admins/tenants/bootstrap which "
        "stays synchronous."
    ),
    summary="Create tenant (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def create_tenant_endpoint(
    tenant_data: TenantCreate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="tenant.create",
        payload=tenant_data.model_dump(exclude_none=True),
        resource_type="tenant",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Tenants fetched successfully",
    description=(
        "List tenants with pagination, filters, sort, free-text search, "
        "and optional facet counts. The unfiltered first page is served "
        "from the global precompute cache; any filter/sort/q/facets/skip "
        "bypasses the cache and hits Mongo directly."
    ),
    summary="List tenants",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_tenants(
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.GLOBAL.value}",
            resource="tenants.list",
            ttl=60,
            loader=_load_tenants,
        )
        # Precompute returns a list; wrap in the standard envelope.
        items = cached if isinstance(cached, list) else cached.get("items", [])
        return {
            "items": items,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": TENANTS_LIST_SPEC.default_limit,
                "hasMore": False,
            },
        }

    query = parse_list_query(request, TENANTS_LIST_SPEC)
    plan_tiers = list(request.query_params.getlist("planTier"))
    sub_statuses = list(request.query_params.getlist("subscriptionStatus"))

    base_filter: dict[str, Any] = {}
    id_match = _q_builds_id_match(query.q)
    if id_match:
        base_filter.update(id_match)

    matched_tids = await _resolve_subscription_tenant_ids(
        plan_tiers=plan_tiers, sub_statuses=sub_statuses
    )
    if matched_tids is not None:
        if not matched_tids:
            return {
                "items": [],
                "meta": {
                    "total": 0,
                    "skip": query.skip,
                    "limit": query.limit,
                    "hasMore": False,
                },
            }
        base_filter["_id"] = {
            "$in": [ObjectId(t) for t in matched_tids if ObjectId.is_valid(t)]
        }

    # Drop sub-side filters from the parsed query so they don't get
    # applied against the tenant collection (where they'd never match).
    query.filters.pop("planTier", None)
    query.filters.pop("subscriptionStatus", None)

    page = await run_list(
        collection=db.tenant_companies,
        query=query,
        base_filter=base_filter,
        map_doc=_map_tenant_doc,
        facet_runner=_status_facet,
    )
    return page


async def _status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    """Tenants need active/inactive/all counts, not raw is_active true/false."""
    if field != "status":
        return {}
    base_filter = {k: v for k, v in filter_doc.items() if k != "is_active"}
    active_total = await collection.count_documents({**base_filter, "is_active": True})
    inactive_total = await collection.count_documents(
        {**base_filter, "is_active": False}
    )
    return {
        "active": active_total,
        "inactive": inactive_total,
        "all": active_total + inactive_total,
    }


async def _load_tenants() -> List[Any]:
    tenants = await retrieve_tenants_with_summary(start=0, stop=100)
    return [
        t.model_dump(mode="json", by_alias=True) if hasattr(t, "model_dump") else t
        for t in tenants
    ]


@router.get("/me/contact")
@document_response(
    message="Organization contact fetched successfully",
    description=(
        "Point-of-contact card for the authenticated user's organization: "
        "the main super admin (name, email, role)."
    ),
    summary="Get organization point of contact",
    success_example={
        "user_id": "507f1f77bcf86cd799439012",
        "full_name": "Ada Obi",
        "email": "ada@acme.com",
        "phone": None,
        "role": "super_admin",
        "source": "main_super_admin",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only super_admin can view the organization contact",
        404: "Organization contact not found",
    },
)
async def get_org_contact_endpoint(
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    """Synchronous read (no queueing) — powers the Settings → Organization
    contact card and the platform-admin org detail POC card."""
    from fastapi import HTTPException

    from services.branch_service import get_org_contact_summary

    contact = await get_org_contact_summary(principal.tenant_id or "")
    if contact is None:
        raise HTTPException(status_code=404, detail="Organization contact not found")
    return contact


@router.get("/{tenant_id}")
@document_response(
    message="Tenant fetched successfully",
    description="Retrieve a specific tenant by ID.",
    summary="Retrieve tenant by ID",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Tenant not found",
    },
)
async def get_tenant_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    if principal.role == "admin":
        pass
    elif principal.role == "super_admin":
        if principal.tenant_id != tenant_id:
            raise auth_permission_denied(permission_key="tenant.read")
    else:
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    return await get_or_compute_entity(
        entity_type="tenant",
        entity_id=tenant_id,
        loader=lambda: retrieve_tenant_by_id_with_summary(tenant_id=tenant_id),
    )


@router.patch("/{tenant_id}")
@document_response(
    message="Tenant update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial tenant update.",
    summary="Update tenant by ID (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def update_tenant_endpoint(
    tenant_id: str,
    tenant_data: TenantUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await enqueue_write(
        writer_key="tenant.update",
        payload=tenant_data.model_dump(exclude_none=True),
        resource_type="tenant",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Bulk endpoints ───────────────────────────────────────────────────


@router.post("/bulk/offboard")
@document_response(
    message="Bulk offboard queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a bulk offboard across the supplied tenant ids. The job "
        "result is available via GET /v1/jobs/{job_id} as a "
        "{succeeded, failed} breakdown."
    ),
    summary="Bulk offboard tenants",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        400: "Validation error (empty ids, malformed id, batch too large)",
        401: "Unauthorized",
        403: "Insufficient permissions",
        409: "Idempotency-Key replay with a different body",
    },
)
async def bulk_offboard_tenants(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/bulk/offboard",
        body=payload,
    )
    if hit is not None:
        return hit.response

    ids = payload.get("ids", [])
    reason = str(payload.get("reason") or "bulk_offboard")[:200]
    atomic = bool(payload.get("atomic", False))
    extras = {"reason": reason, "actor_id": actor_id}
    response = await enqueue_bulk_write(
        writer_key="tenant.bulk_offboard",
        ids=ids,
        resource_type="tenant",
        extras=extras,
        atomic=atomic,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/tenants/bulk/offboard",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


# Legacy non-bulk single-item offboard endpoint lives in admin_route.
# The bulk endpoint above complements it; both write the same audit
# action ``tenant.offboarded`` per id (the bulk writer iterates).
_ = Query  # keep Query import live for future filter additions
