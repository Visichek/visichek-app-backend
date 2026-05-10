from __future__ import annotations

"""
PlanEnforcementMiddleware
=========================
Intercepts all authenticated requests and enforces the tenant's subscription plan:

1. **Feature gating** — blocks access to disabled endpoints
2. **CRUD quotas** — limits create/update/delete operations per period
3. **Retrieval quotas** — limits read operations per period
4. **Entity caps** — limits on total entities (system_users, departments, etc.)

Runs AFTER authentication middleware (so we have a user identity) but BEFORE
the actual route handler. Uses Redis-cached plan data for sub-millisecond
lookups on the hot path.

Enforcement is skipped for:
- Application admin role (platform operators)
- Unauthenticated requests (handled by auth layer)
- Health/root endpoints
- Plan management endpoints themselves (to avoid circular blocking)
"""

import fnmatch
import logging
import time
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from core.response_envelope import error_response
from repositories.tokens_repo import get_access_token_allow_expired
from security.principal import APP_ROLES

logger = logging.getLogger(__name__)


# Paths that bypass plan enforcement entirely
EXEMPT_PATH_PREFIXES = (
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/v1/admins/",  # Application admin endpoints
    "/v1/plans/",  # Plan management (admin-only anyway)
    "/v1/subscriptions/",  # Subscription management
    "/v1/discounts/",  # Discount management
    "/v1/usage/",  # Usage reporting
    # Support cases are a hard product rule (10-open cap), not metered quota.
    # Tenants must always be able to reach support even on suspended plans.
    "/v1/support-cases/",
    "/v1/admins/support-cases/",
    # Self-onboarding lead-capture (unauthenticated submission + tenant
    # self-completion path post-acceptance). Admin management endpoints
    # under /v1/tenants/onboarding are already covered by the admin role
    # short-circuit in the middleware.
    "/v1/onboarding/",
    # KYC routes are public-facing (kiosk + webhook). Per-tenant
    # availability is checked inside ``services.kyc_service`` against
    # the plan's feature_rules — we don't need (and shouldn't have)
    # the middleware blocking the kiosk before it can decide.
    "/v1/kyc/",
)

# Map HTTP methods to operation types for quota checking
METHOD_TO_OPERATION = {
    "POST": "create",
    "GET": "read",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

# Map URL path segments to collection names for quota matching
# This maps the resource in the URL to the collection name used in plan limits
PATH_TO_COLLECTION = {
    "visitors": "visitors",
    "visitor-profiles": "visitor_profiles",
    "appointments": "appointments",
    "departments": "departments",
    "system-users": "system_users",
    "documents": "documents",
    "privacy-notices": "privacy_notices",
    "dashboard": "dashboard",
    "data-subject-requests": "data_subject_requests",
    "retention": "retention",
    "sub-processors": "sub_processors",
    "compliance": "compliance",
    "audit": "audit",
    "incidents": "incidents",
    "tenants": "tenants",
    "branches": "branches",
    "checkins": "checkins",
    "badges": "badges",
}


def _extract_collection_from_path(path: str) -> Optional[str]:
    """Extract the resource collection name from a URL path.
    e.g. /v1/visitors/abc123 -> 'visitors'
    """
    parts = path.strip("/").split("/")
    # Skip version prefix (v1)
    if len(parts) >= 2 and parts[0].startswith("v"):
        resource = parts[1]
        return PATH_TO_COLLECTION.get(resource)
    return None


def _is_exempt_path(path: str) -> bool:
    """Check if the path is exempt from enforcement.

    Matches both the prefix form (``/v1/plans/something``) and the exact
    collection form (``/v1/plans``). The second case matters because the
    route files register collection endpoints at ``""`` / no trailing slash,
    and without this every hit on ``/v1/plans`` would fall through to
    subscription-required enforcement — exactly the loop we're trying to
    avoid for admin-only management endpoints.
    """
    for prefix in EXEMPT_PATH_PREFIXES:
        if prefix == "/":
            if path == "/":
                return True
            continue
        if path.startswith(prefix):
            return True
        # Allow the no-trailing-slash form of a slash-suffixed prefix
        # (e.g. prefix="/v1/plans/" matches path="/v1/plans").
        if prefix.endswith("/") and path == prefix[:-1]:
            return True
    return False


def _check_feature_access(
    path: str,
    method: str,
    feature_rules: list,
) -> tuple[bool, Optional[str]]:
    """Check if the endpoint is allowed by the plan's feature rules.
    Returns (allowed, reason).
    """
    if not feature_rules:
        return True, None  # No rules = everything allowed

    for rule in feature_rules:
        pattern = rule.get("endpoint_pattern", "")
        methods = rule.get("methods", ["GET", "POST", "PUT", "DELETE"])
        enabled = rule.get("enabled", True)

        # Check if this rule matches the current request
        if fnmatch.fnmatch(path, pattern) and method.upper() in methods:
            if not enabled:
                desc = rule.get("description", "Feature not available on your plan")
                return False, desc

    return True, None


class PlanEnforcementMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from core.settings import get_settings

        if get_settings().env == "testing":
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Skip exempt paths
        if _is_exempt_path(path):
            return await call_next(request)

        # Skip OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)

        # Extract auth info to determine tenant
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Unauthenticated — let auth layer handle rejection
            return await call_next(request)

        token = auth_header.split(" ", maxsplit=1)[1]
        access_token = await get_access_token_allow_expired(accessToken=token)
        if not access_token:
            return await call_next(request)

        role = (access_token.role or "").lower()

        # Application admins bypass plan enforcement
        if role in APP_ROLES:
            return await call_next(request)

        # Get tenant_id from the token
        tenant_id = getattr(access_token, "tenant_id", None)
        if not tenant_id:
            # No tenant association — can't enforce, let it through
            return await call_next(request)

        # Resolve the tenant's plan (cached). Every tenant should have at
        # least a Free-plan subscription (see services.plan_bootstrap).
        # If we still find nothing here, lazily provision Free instead
        # of breaking the request — this protects new tenants whose
        # bootstrap auto-subscribe failed and migrated tenants the
        # backfill missed.
        from services.plan_cache_service import resolve_tenant_plan

        plan_data = await resolve_tenant_plan(tenant_id)
        if not plan_data:
            request_id = getattr(request.state, "request_id", None)
            logger.info(
                "plan_enforcement: lazy free-plan provision tenant_id=%s path=%s",
                tenant_id,
                path,
            )
            try:
                from services.plan_bootstrap import (
                    ensure_tenant_default_subscription,
                )

                await ensure_tenant_default_subscription(tenant_id)
                plan_data = await resolve_tenant_plan(tenant_id)
            except Exception:
                logger.exception(
                    "lazy free-plan provision failed tenant_id=%s",
                    tenant_id,
                )

        if not plan_data:
            request_id = getattr(request.state, "request_id", None)
            logger.warning(
                "subscription_required_block",
                extra={
                    "tenant_id": tenant_id,
                    "user_id": getattr(access_token, "userId", None),
                    "role": role,
                    "path": path,
                    "method": method,
                    "request_id": request_id,
                },
            )
            return error_response(
                status_code=402,
                message="No active subscription",
                data={
                    "code": "SUBSCRIPTION_REQUIRED",
                    "details": "Your organization does not have an active subscription plan.",
                    "tenant_id": tenant_id,
                    "next_action": "subscribe",
                },
                request_id=request_id,
            )

        # Check subscription status
        sub_status = plan_data.get("subscription_status", "")
        if sub_status not in ("active", "trialing"):
            return error_response(
                status_code=402,
                message="Subscription inactive",
                data={
                    "code": "SUBSCRIPTION_INACTIVE",
                    "details": f"Your subscription status is '{sub_status}'. Please renew.",
                },
                request_id=getattr(request.state, "request_id", None),
            )

        # Check trial expiry
        trial_ends = plan_data.get("trial_ends_at")
        if sub_status == "trialing" and trial_ends and int(time.time()) > trial_ends:
            return error_response(
                status_code=402,
                message="Trial expired",
                data={
                    "code": "TRIAL_EXPIRED",
                    "details": "Your trial period has ended. Please subscribe to continue.",
                },
                request_id=getattr(request.state, "request_id", None),
            )

        # 0. Enterprise sub-app gating — paths under /v1/enterprise/<slug>/*
        # are only accessible to tenants whose active subscription points
        # to an enterprise plan whose ``name`` equals the slug. This is
        # checked BEFORE generic feature gating so the error message is
        # specific ("you need the X enterprise plan") rather than a
        # generic "feature disabled".
        from core.enterprise_apps import (
            extract_enterprise_slug,
            is_enterprise_app_path,
        )

        if is_enterprise_app_path(path):
            requested_slug = extract_enterprise_slug(path)
            current_plan_name = plan_data.get("plan_name", "") or ""
            current_tier = plan_data.get("tier", "") or ""
            if (
                not requested_slug
                or current_tier != "enterprise"
                or current_plan_name != requested_slug
            ):
                return error_response(
                    status_code=403,
                    message="Enterprise plan mismatch",
                    data={
                        "code": "ENTERPRISE_PLAN_MISMATCH",
                        "details": (
                            f"This endpoint is reserved for the '{requested_slug}' "
                            "enterprise plan. Your current plan does not include it."
                        ),
                        "required_plan_name": requested_slug,
                        "plan": plan_data.get("plan_display_name"),
                        "tier": current_tier,
                    },
                    request_id=getattr(request.state, "request_id", None),
                )

        # 1. Feature gating
        allowed, reason = _check_feature_access(
            path,
            method,
            plan_data.get("feature_rules", []),
        )
        if not allowed:
            return error_response(
                status_code=403,
                message="Feature not available",
                data={
                    "code": "FEATURE_DISABLED",
                    "details": reason,
                    "plan": plan_data.get("plan_display_name"),
                    "tier": plan_data.get("tier"),
                },
                request_id=getattr(request.state, "request_id", None),
            )

        # 2. CRUD + Retrieval quota enforcement
        collection = _extract_collection_from_path(path)
        operation = METHOD_TO_OPERATION.get(method.upper())
        subscription_id = plan_data.get("subscription_id", "")

        if collection and operation:
            from services.usage_service import check_quota, record_usage, get_period_key
            from schemas.plan_schema import QuotaResetInterval
            from schemas.usage_schema import OperationType

            quota_exceeded = False
            quota_details = {}

            if operation in ("create", "update", "delete"):
                # Check CRUD limits
                for cl in plan_data.get("crud_limits", []):
                    if cl.get("collection") == collection:
                        limit_key = f"max_{operation}"
                        limit_value = cl.get(limit_key)
                        reset_interval = QuotaResetInterval(
                            cl.get("reset_interval", "monthly")
                        )
                        allowed, current, cap = await check_quota(
                            tenant_id,
                            subscription_id,
                            collection,
                            operation,
                            limit_value,
                            reset_interval,
                        )
                        if not allowed:
                            quota_exceeded = True
                            quota_details = {
                                "collection": collection,
                                "operation": operation,
                                "current": current,
                                "limit": cap,
                                "reset_interval": reset_interval.value,
                            }
                        break

            elif operation == "read":
                # Check retrieval quotas
                for rq in plan_data.get("retrieval_quotas", []):
                    if rq.get("collection") == collection:
                        limit_value = rq.get("max_reads")
                        reset_interval = QuotaResetInterval(
                            rq.get("reset_interval", "daily")
                        )
                        allowed, current, cap = await check_quota(
                            tenant_id,
                            subscription_id,
                            collection,
                            "read",
                            limit_value,
                            reset_interval,
                        )
                        if not allowed:
                            quota_exceeded = True
                            quota_details = {
                                "collection": collection,
                                "operation": "read",
                                "current": current,
                                "limit": cap,
                                "reset_interval": reset_interval.value,
                            }
                        break

            if quota_exceeded:
                return error_response(
                    status_code=429,
                    message="Quota exceeded",
                    data={
                        "code": "QUOTA_EXCEEDED",
                        "details": quota_details,
                        "plan": plan_data.get("plan_display_name"),
                        "tier": plan_data.get("tier"),
                    },
                    headers={"Retry-After": "3600"},
                    request_id=getattr(request.state, "request_id", None),
                )

            # Record usage (fire-and-forget style, but we await for accuracy)
            try:
                reset_interval_str = "monthly"  # default
                for cl in plan_data.get("crud_limits", []):
                    if cl.get("collection") == collection:
                        reset_interval_str = cl.get("reset_interval", "monthly")
                        break
                for rq in plan_data.get("retrieval_quotas", []):
                    if rq.get("collection") == collection:
                        reset_interval_str = rq.get("reset_interval", "daily")
                        break

                period_key = get_period_key(QuotaResetInterval(reset_interval_str))
                await record_usage(
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    collection=collection,
                    operation=OperationType(operation),
                    endpoint=path,
                    user_id=access_token.userId,
                    user_role=role,
                    period_key=period_key,
                )
            except Exception:
                pass  # Usage recording failure shouldn't block the request

        # Store plan data on request state for downstream use
        request.state.tenant_plan = plan_data
        request.state.tenant_id = tenant_id

        response = await call_next(request)

        # Add plan info headers
        response.headers["X-Plan-Tier"] = plan_data.get("tier", "unknown")
        response.headers["X-Subscription-Status"] = plan_data.get(
            "subscription_status", "unknown"
        )

        return response
