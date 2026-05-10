from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status
from pydantic import BaseModel, Field

from core.bulk import enqueue_bulk_write
from core.database import db
from core.errors import auth_permission_denied, auth_role_mismatch
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.subscription_schema import (
    BillingCycle,
)
from services.subscription_service import (
    retrieve_subscription_by_id,
    retrieve_subscriptions_with_details,
    retrieve_tenant_active_subscription,
)
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token
from security.principal import AuthPrincipal

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


SUBS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"renews_at", "current_period_end", "date_created", "status", "effective_price"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("tenant_id", "plan_id"),
    filters={
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
        "planId": FilterDef(name="planId", mongo_field="plan_id"),
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=frozenset(
                {"active", "trialing", "past_due", "cancelled", "suspended", "expired"}
            ),
        ),
        "billingCycle": FilterDef(
            name="billingCycle",
            mongo_field="billing_cycle",
            allowed_values=frozenset({"monthly", "yearly"}),
        ),
    },
    range_filters={
        "renewsAt": "current_period_end",
        "createdAt": "date_created",
    },
    facet_fields=frozenset({"status"}),
)


def _is_default_sub_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(SUBS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(SUBS_LIST_SPEC.default_limit)


def _map_sub_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _subs_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for status_value in (
        "active",
        "trialing",
        "past_due",
        "cancelled",
        "suspended",
        "expired",
    ):
        out[status_value] = await collection.count_documents(
            {**base, "status": status_value}
        )
    out["all"] = sum(out.values())
    return out


# --- Request bodies ---


class SubscribeTenantRequest(BaseModel):
    tenant_id: str
    plan_id: str
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    discount_ids: list[str] = Field(default_factory=list)
    trial_days: int = 0
    admin_notes: Optional[str] = None
    feature_overrides: Optional[dict] = None
    crud_limit_overrides: Optional[dict] = None
    retrieval_quota_overrides: Optional[dict] = None
    tenant_cap_overrides: Optional[dict] = None


class ChangePlanRequest(BaseModel):
    tenant_id: str
    new_plan_id: str
    billing_cycle: Optional[BillingCycle] = None


class CancelSubscriptionRequest(BaseModel):
    tenant_id: str
    reason: Optional[str] = None
    immediate: bool = False


class UpdateOverridesRequest(BaseModel):
    feature_overrides: Optional[dict] = None
    crud_limit_overrides: Optional[dict] = None
    retrieval_quota_overrides: Optional[dict] = None
    tenant_cap_overrides: Optional[dict] = None


# --- Endpoints ---


@router.post("")
@document_response(
    message="Subscription creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a subscription create. The real subscription id is returned via "
        "``GET /v1/jobs/{job_id}`` once the worker commits — the 202 body's ``id`` "
        "is speculative for subscription writes."
    ),
    summary="Create subscription (async)",
)
async def create_subscription_endpoint(
    payload: SubscribeTenantRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.create",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Subscriptions retrieved successfully",
    description=(
        "Paginated subscription list with filters, sort, q, and optional "
        "facets. Unfiltered first page is served from the global "
        "precompute cache."
    ),
    summary="List subscriptions",
    include_meta=True,
)
async def list_subscriptions_endpoint(
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_sub_listing(request):
        cached: List[Any] = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="subscriptions.list",
            ttl=60,
            loader=_load_subscriptions,
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: SUBS_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": SUBS_LIST_SPEC.default_limit,
                "hasMore": len(items) > SUBS_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, SUBS_LIST_SPEC)
    return await run_list(
        collection=db.subscriptions,
        query=query,
        map_doc=_map_sub_doc,
        facet_runner=_subs_status_facet,
    )


async def _load_subscriptions() -> List[Any]:
    subs = await retrieve_subscriptions_with_details(start=0, stop=100)
    return [
        s.model_dump(mode="json", by_alias=True) if hasattr(s, "model_dump") else s
        for s in subs
    ]


@router.get("/tenant/{tenant_id}/active")
@document_response(
    message="Active subscription retrieved",
    description="Served from the per-tenant precompute cache.",
    summary="Get tenant active subscription",
)
async def get_tenant_active_subscription_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    if principal.role == "admin":
        pass
    elif principal.role == "super_admin":
        if principal.tenant_id != tenant_id:
            raise auth_permission_denied(permission_key="subscription.read")
    else:
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)

    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="subscription.active",
        ttl=60,
        loader=lambda: _load_active_subscription(tenant_id),
    )


async def _load_active_subscription(tenant_id: str) -> Any:
    """Load the tenant's active subscription enriched with a plan summary.

    Without the plan summary, a frontend looking at the response after a
    cancel cannot tell that the tenant has been auto-downgraded to Free
    (the response just shows ``status: active`` with a fresh subscription
    id and effectivePrice 0.0). Embedding the plan name + tier here makes
    the "you're on Free now" state directly readable, and the
    ``isFreeFallback`` flag is a single boolean the FE can branch on to
    render "Plan cancelled, you're on Free".
    """
    from bson import ObjectId as _BsonObjectId

    from config.plan_tiers import FREE_PLAN_NAME
    from repositories.plan_repo import get_plan

    sub = await retrieve_tenant_active_subscription(tenant_id)
    if not sub:
        return None

    payload: dict = (
        sub.model_dump(mode="json", by_alias=True)
        if hasattr(sub, "model_dump")
        else dict(sub)
    )

    plan_id_str = sub.plan_id if hasattr(sub, "plan_id") else payload.get("planId")
    plan_summary: Optional[dict] = None
    is_free_fallback = False
    if plan_id_str and _BsonObjectId.is_valid(plan_id_str):
        plan = await get_plan({"_id": _BsonObjectId(plan_id_str)})
        if plan is not None:
            plan_summary = {
                "id": plan.id,
                "name": plan.name,
                "displayName": plan.display_name,
                "tier": plan.tier.value
                if hasattr(plan.tier, "value")
                else plan.tier,
                "basePriceMonthly": plan.base_price_monthly,
                "basePriceYearly": plan.base_price_yearly,
                "currency": plan.currency,
            }
            is_free_fallback = plan.name == FREE_PLAN_NAME and bool(
                (sub.admin_notes or "").startswith("Auto-downgraded")
            )

    payload["plan"] = plan_summary
    payload["isFreeFallback"] = is_free_fallback
    return payload


@router.get("/{subscription_id}")
@document_response(
    message="Subscription retrieved successfully",
    summary="Get subscription",
)
async def get_subscription_endpoint(
    subscription_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    return await get_or_compute_entity(
        entity_type="subscription",
        entity_id=subscription_id,
        loader=lambda: retrieve_subscription_by_id(subscription_id),
    )


@router.post("/change-plan")
@document_response(
    message="Plan change queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Change plan (async)",
)
async def change_plan_endpoint(
    payload: ChangePlanRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.change_plan",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/cancel")
@document_response(
    message="Cancel subscription queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Cancel a tenant's subscription. Tenant super_admins may cancel "
        "their own tenant only (the path verifies ``principal.tenant_id == "
        "payload.tenant_id``). Application admins may cancel any tenant. "
        "Other roles get HTTP 403."
    ),
    summary="Cancel subscription (async)",
)
async def cancel_subscription_endpoint(
    payload: CancelSubscriptionRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    # Authorisation: app admins → any tenant; super_admin → own tenant only.
    if principal.role == "admin":
        actor_role = "admin"
    elif principal.role == "super_admin":
        if not principal.tenant_id or principal.tenant_id != payload.tenant_id:
            raise auth_permission_denied(permission_key="subscription.cancel")
        actor_role = "super_admin"
    else:
        raise auth_role_mismatch(
            required_role="admin", actual_role=principal.role
        )

    return await enqueue_write(
        writer_key="subscription.cancel",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=principal.user_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/bulk/cancel")
@document_response(
    message="Bulk subscription cancel queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Cancel multiple subscriptions with one shared reason. The frontend "
        "collects one reason per batch in a modal — backend does NOT accept "
        "per-id reasons."
    ),
    summary="Bulk cancel subscriptions",
)
async def bulk_cancel_subscriptions(
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
        route="POST /v1/subscriptions/bulk/cancel",
        body=payload,
    )
    if hit is not None:
        return hit.response
    reason = str(payload.get("reason") or "")[:500]
    immediate = bool(payload.get("immediate", False))
    response = await enqueue_bulk_write(
        writer_key="subscription.bulk_cancel",
        ids=payload.get("ids", []),
        resource_type="subscription",
        extras={"reason": reason, "immediate": immediate},
        atomic=bool(payload.get("atomic", False)),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/subscriptions/bulk/cancel",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.put("/{subscription_id}/overrides")
@document_response(
    message="Subscription override update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update subscription overrides (async)",
)
async def update_overrides_endpoint(
    subscription_id: str,
    payload: UpdateOverridesRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.update_overrides",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        resource_id=subscription_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
