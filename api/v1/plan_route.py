from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Query, Request, status
from pydantic import BaseModel

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.plan_schema import (
    PlanCreate,
    PlanUpdate,
)
from services.plan_service import retrieve_plan_by_id, retrieve_plans
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/plans", tags=["Plans"])


def _plan_status_builder(values):
    if "all" in values:
        return {}
    if len(values) == 1:
        return {"status": values[0]}
    return {"status": {"$in": list(values)}}


PLANS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"display_name", "name", "base_price_monthly", "date_created", "status", "tier"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("name", "display_name"),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=frozenset({"draft", "active", "archived", "all"}),
            builder=_plan_status_builder,
        ),
        "tier": FilterDef(
            name="tier",
            multi=True,
            allowed_values=frozenset({"free", "starter", "professional", "enterprise", "custom"}),
        ),
        "includedFeature": FilterDef(
            name="includedFeature",
            mongo_field="feature_rules.endpoint_pattern",
        ),
    },
    facet_fields=frozenset({"status"}),
)


def _is_default_plan_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit", "public_only"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(PLANS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(PLANS_LIST_SPEC.default_limit)


def _map_plan_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _plans_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for status_value in ("active", "draft", "archived"):
        out[status_value] = await collection.count_documents(
            {**base, "status": status_value}
        )
    out["all"] = sum(out.values())
    return out


class PlanFeatureToggleRequest(BaseModel):
    """Body for ``POST /v1/plans/{plan_id}/features/{feature_key}``."""

    enabled: bool


class PlanFeatureCatalogEntry(BaseModel):
    """Frontend rendering hint for the plan-features checklist."""

    key: str
    label: str
    description: str
    endpoint_pattern: str
    methods: list[str]
    default_enabled: bool
    requires_external_config: bool
    external_config_hint: Optional[str] = None


@router.post("")
@document_response(
    message="Plan creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a plan creation (application admin only).",
    summary="Create plan (async)",
)
async def create_plan_endpoint(
    payload: PlanCreate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.create",
        payload=payload.model_dump(exclude_none=True),
        resource_type="plan",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Plans retrieved successfully",
    description=(
        "Paginated plan catalogue with filters, sort, q, and optional "
        "facets. The unfiltered first page (no status/tier/q/sort/facets) "
        "is served from the global precompute cache."
    ),
    summary="List plans",
    include_meta=True,
)
async def list_plans_endpoint(
    request: Request,
    public_only: bool = Query(False),
) -> Any:
    if _is_default_plan_listing(request):
        resource = "plans.public_list" if public_only else "plans.list"
        cached: List[Any] = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource=resource,
            ttl=120,
            loader=lambda: _load_plans(public_only),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: PLANS_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": PLANS_LIST_SPEC.default_limit,
                "hasMore": len(items) > PLANS_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, PLANS_LIST_SPEC)
    base_filter: dict[str, Any] = {}
    if public_only:
        base_filter["is_public"] = True
        # public_only callers also exclude archived implicitly
        if "status" not in query.filters:
            base_filter["status"] = {"$ne": "archived"}
    return await run_list(
        collection=db.plans,
        query=query,
        base_filter=base_filter,
        map_doc=_map_plan_doc,
        facet_runner=_plans_status_facet,
    )


async def _load_plans(public_only: bool) -> List[Any]:
    plans = await retrieve_plans(public_only=public_only, start=0, stop=100)
    return [
        p.model_dump(mode="json", by_alias=True) if hasattr(p, "model_dump") else p
        for p in plans
    ]


@router.get("/{plan_id}")
@document_response(
    message="Plan retrieved successfully",
    summary="Get plan",
)
async def get_plan_endpoint(plan_id: str) -> Any:
    return await get_or_compute_entity(
        entity_type="plan",
        entity_id=plan_id,
        loader=lambda: retrieve_plan_by_id(plan_id),
    )


@router.put("/{plan_id}")
@document_response(
    message="Plan update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a plan update. Subscribed tenants get a plan-cache refresh on commit.",
    summary="Update plan (async)",
)
async def update_plan_endpoint(
    plan_id: str,
    payload: PlanUpdate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.update",
        payload=payload.model_dump(exclude_none=True),
        resource_type="plan",
        resource_id=plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{plan_id}/activate")
@document_response(
    message="Plan activation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Activate plan (async)",
)
async def activate_plan_endpoint(
    plan_id: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.activate",
        payload={},
        resource_type="plan",
        resource_id=plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{plan_id}/archive")
@document_response(
    message="Plan archival queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Archive plan (async)",
)
async def archive_plan_endpoint(
    plan_id: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.archive",
        payload={},
        resource_type="plan",
        resource_id=plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{source_plan_id}/clone")
@document_response(
    message="Plan clone queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a plan clone. A new plan id is assigned by the worker.",
    summary="Clone plan (async)",
)
async def clone_plan_endpoint(
    source_plan_id: str,
    request: Request,
    new_name: str = Query(...),
    new_display_name: str = Query(...),
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.clone",
        payload={"new_name": new_name, "new_display_name": new_display_name},
        resource_type="plan",
        resource_id=source_plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{plan_id}")
@document_response(
    message="Plan deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete plan (async)",
)
async def delete_plan_endpoint(
    plan_id: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.delete",
        payload={},
        resource_type="plan",
        resource_id=plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ── Plan-feature toggles ─────────────────────────────────────────────
#
# Convenience endpoints over the togglable-feature catalog defined in
# ``services.plan_feature_service``. The frontend renders the catalog
# as a checklist on the plan editor page; toggling any item POSTs to
# ``/{plan_id}/features/{feature_key}`` with ``{ enabled: bool }``.
# This avoids the frontend having to PATCH the entire ``feature_rules``
# array just to flip one flag.


@router.get(
    "/features/catalog",
    response_model=List[PlanFeatureCatalogEntry],
)
@document_response(
    message="Plan feature catalog retrieved",
    description=(
        "List the togglable features available on every plan. The "
        "frontend renders this as a checklist on the plan editor "
        "page. New features added in ``TOGGLEABLE_FEATURES`` "
        "automatically surface here."
    ),
    summary="List togglable plan features",
)
async def list_plan_features_endpoint() -> List[PlanFeatureCatalogEntry]:
    from services.plan_feature_service import get_feature_catalog

    return [
        PlanFeatureCatalogEntry(
            key=spec.key,
            label=spec.label,
            description=spec.description,
            endpoint_pattern=spec.endpoint_pattern,
            methods=list(spec.methods),
            default_enabled=spec.default_enabled,
            requires_external_config=spec.requires_external_config,
            external_config_hint=spec.external_config_hint,
        )
        for spec in get_feature_catalog()
    ]


# ─── Bulk endpoints ───────────────────────────────────────────────────


def _bulk_response_for(
    *,
    request: Request,
    payload: dict,
    idempotency_key: Optional[str],
    admin: Any,
    writer_key: str,
    extras: Optional[dict] = None,
    route_label: str,
):
    actor_id = getattr(admin, "id", None)
    actor_role = "admin"
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key, scope=scope, route=route_label, body=payload
    )
    if hit is not None:
        return hit, actor_id, actor_role, scope
    return None, actor_id, actor_role, scope


@router.post("/bulk/activate")
@document_response(
    message="Bulk plan activation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk activate plans",
)
async def bulk_activate_plans(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    cached, actor_id, actor_role, scope = _bulk_response_for(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        admin=admin,
        writer_key="plan.bulk_activate",
        route_label="POST /v1/plans/bulk/activate",
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="plan.bulk_activate",
        ids=payload.get("ids", []),
        resource_type="plan",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/plans/bulk/activate",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/archive")
@document_response(
    message="Bulk plan archive queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk archive plans",
)
async def bulk_archive_plans(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    cached, actor_id, actor_role, scope = _bulk_response_for(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        admin=admin,
        writer_key="plan.bulk_archive",
        route_label="POST /v1/plans/bulk/archive",
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="plan.bulk_archive",
        ids=payload.get("ids", []),
        resource_type="plan",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/plans/bulk/archive",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/delete")
@document_response(
    message="Bulk plan delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete plans (DRAFT only — server enforces)",
)
async def bulk_delete_plans(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin=Depends(check_admin_account_status_and_permissions),
):
    cached, actor_id, actor_role, scope = _bulk_response_for(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        admin=admin,
        writer_key="plan.bulk_delete",
        route_label="POST /v1/plans/bulk/delete",
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="plan.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="plan",
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/plans/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/{plan_id}/features/{feature_key}")
@document_response(
    message="Plan feature toggle queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enable or disable one named feature on the given plan. "
        "Behaves like a flag: enabling an already-enabled feature is a "
        "no-op (no audit row). Subscribed tenants get a plan-cache "
        "fanout on commit so the new gate takes effect within seconds."
    ),
    summary="Toggle plan feature (async)",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        404: "Unknown feature_key — call /v1/plans/features/catalog for the list",
    },
)
async def toggle_plan_feature_endpoint(
    plan_id: str,
    feature_key: str,
    payload: PlanFeatureToggleRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="plan.set_feature",
        payload={
            "feature_key": feature_key,
            "enabled": payload.enabled,
            "_actor_id": getattr(admin, "id", None) or "",
            "_actor_role": "admin",
            "_request_id": getattr(request.state, "request_id", None),
        },
        resource_type="plan",
        resource_id=plan_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
