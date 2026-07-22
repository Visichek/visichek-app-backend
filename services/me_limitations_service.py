"""Build the per-request "limitations" payload consumed by GET /v1/me/limitations.

The frontend calls this once at app load (and after any plan change) to
learn — without an enumeration of trial-and-error 403s — which features
are denied, which entity caps are in force, and which existing entities
are LOCKED because the tenant has more of them than the current plan
allows.

Shape (camelCase via the global case-conversion middleware):

    {
      "tenantId": "...",
      "plan": {
        "id": "...",
        "name": "free",
        "displayName": "Free",
        "tier": "free",
        "isFreeFallback": false,
        "subscriptionStatus": "active",
        "currentPeriodEnd": 1234567890
      },
      "caps": {
        "maxBranches": 1,
        "maxDepartments": 1,
        "maxSystemUsers": 1,
        "maxVisitorsPerMonth": 50,
        "maxAppointmentsPerMonth": 0
      },
      "deniedEndpoints": [
        { "pattern": "/v1/appointments*", "methods": ["GET","POST",...],
          "description": "Appointments require Premium or Enterprise" },
        ...
      ],
      "deniedFeatures": [
        "appointments", "hosts", "badges", "branding", "kyc",
        "csv_export", "host_email_notifications", "multi_location"
      ],
      "lockedEntities": {
        "branches": ["<branch_id>", ...],     // non-HQ branches when on Free
        "departments": ["<dept_id>", ...]      // departments above the cap
      },
      "enterprise": {
        "isEnterprise": false,
        "subAppPrefix": null   // "/v1/enterprise/<slug>" when on enterprise
      }
    }

Frontend rule of thumb:
    * Any route under ``deniedEndpoints[*].pattern`` should be hidden
      from nav/UI. Don't try the call to "see what happens".
    * ``lockedEntities.branches`` / ``lockedEntities.departments`` —
      render those rows as locked (greyed out, no actions) in the
      relevant list pages.
    * ``enterprise.subAppPrefix`` — if non-null, the tenant has access
      to custom endpoints under that prefix. Otherwise hide all UI
      that would call ``/v1/enterprise/*``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from config.plan_tiers import FREE_PLAN_NAME
from core.database import db
from repositories.plan_repo import get_plan
from security.principal import AuthPrincipal
from services.plan_cache_service import resolve_tenant_plan

logger = logging.getLogger(__name__)


# Maps fnmatch endpoint patterns to short feature keys the frontend uses
# to flip nav items on/off. Keep in sync with the deny lists in
# ``config/plan_tiers.py``. A single pattern may map to multiple
# feature keys when the same route surface backs more than one UI
# concept (rare).
_ENDPOINT_TO_FEATURE_KEY: Dict[str, str] = {
    "/v1/appointments*": "appointments",
    "/v1/appointments/*": "appointments",
    "/v1/hosts*": "hosts",
    "/v1/hosts/*": "hosts",
    "/v1/branding*": "branding",
    "/v1/branding/*": "branding",
    "/v1/badges*": "badges",
    "/v1/badges/*": "badges",
    "/v1/kyc*": "kyc",
    "/v1/kyc/*": "kyc",
    "/v1/dashboard/export*": "csv_export",
    "/v1/compliance/export*": "csv_export",
    "/v1/notifications/preferences": "host_email_notifications",
    "/v1/branches": "multi_location",
    "/v1/branches/*": "multi_location",
    "/v1/watchlist*": "watchlist",
    "/v1/watchlist/*": "watchlist",
    "/v1/sso*": "sso",
    "/v1/sso/*": "sso",
}


# Feature keys that do NOT map to a single endpoint pattern — they
# gate sub-sections of shared endpoints (e.g. fields inside
# ``PATCH /v1/tenants/{id}/settings``). Listed here per tier so the
# frontend can hide / lock the matching UI controls. The server-side
# enforcement lives in the writer for each shared endpoint (see
# ``services/tenant_settings_writer.py`` for the matching field-strip
# logic that backs these keys).
_EXTRA_FEATURE_KEYS_BY_TIER: Dict[str, tuple[str, ...]] = {
    "free": (
        "email_preferences",
        "visitor_policies",
        "geofencing",
        # Insights / analytics capability keys. The Insights UI greys the
        # matching controls + cards and shows an upgrade tooltip. Server-side
        # enforcement is authoritative in services/insights_service.py — these
        # keys only let the FE pre-lock nav without a round-trip.
        "analytics.customRange",
        "analytics.roleTabs",
        "analytics.hourly",
        "analytics.compliance",
        "analytics.audit",
        "analytics.export",
        "analytics.trends",
    ),
}

# Free-tier analytics ceilings advertised in ``caps`` so the FE can pre-bound
# the range picker / top-list without a round-trip. Authoritative enforcement
# is in services/insights_service.py. Absent on paid tiers = unlimited.
_ANALYTICS_CAPS_BY_TIER: Dict[str, Dict[str, int]] = {
    "free": {"analyticsMaxRangeDays": 7, "topListMax": 3},
}


async def _list_locked_branch_ids(tenant_id: str) -> List[str]:
    """Branches the plan's ``max_branches`` cap excludes.

    HQ is the canonical "kept" branch on Free. We pick the oldest HQ
    flag-true branch as the keeper; everything else is locked. Stable
    order so frontends see the same locked set across reloads.
    """
    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return []
    caps = plan_data.get("tenant_caps") or {}
    cap = caps.get("max_branches")
    if cap is None:
        return []  # Unlimited

    cursor = (
        db["branches"]
        .find({"tenant_id": tenant_id}, projection={"_id": 1, "is_headquarters": 1})
        .sort([("is_headquarters", -1), ("date_created", 1)])
    )
    seen = 0
    locked: List[str] = []
    async for doc in cursor:
        seen += 1
        if seen > int(cap):
            _id = doc.get("_id")
            if _id is not None:
                locked.append(str(_id))
    return locked


async def _list_locked_department_ids(tenant_id: str) -> List[str]:
    """Departments the plan's ``max_departments`` cap excludes."""
    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return []
    caps = plan_data.get("tenant_caps") or {}
    cap = caps.get("max_departments")
    if cap is None:
        return []

    cursor = (
        db["departments"]
        .find({"tenant_id": tenant_id}, projection={"_id": 1})
        .sort("date_created", 1)
    )
    seen = 0
    locked: List[str] = []
    async for doc in cursor:
        seen += 1
        if seen > int(cap):
            _id = doc.get("_id")
            if _id is not None:
                locked.append(str(_id))
    return locked


def _denied_endpoints_from_plan(plan_data: dict) -> List[Dict[str, Any]]:
    """Extract the disabled feature_rules for the FE to hide."""
    out: List[Dict[str, Any]] = []
    for rule in plan_data.get("feature_rules", []):
        if rule.get("enabled", True):
            continue
        out.append(
            {
                "pattern": rule.get("endpoint_pattern", ""),
                "methods": rule.get("methods")
                or ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "description": rule.get("description") or "",
            }
        )
    return out


def _denied_feature_keys(denied_endpoints: List[Dict[str, Any]]) -> List[str]:
    """Map deny rules into stable short keys for frontend feature flags."""
    keys: set[str] = set()
    for entry in denied_endpoints:
        key = _ENDPOINT_TO_FEATURE_KEY.get(entry.get("pattern", ""))
        if key:
            keys.add(key)
    return sorted(keys)


async def _build_plan_block(
    tenant_id: str, plan_data: Optional[dict]
) -> Optional[Dict[str, Any]]:
    if not plan_data:
        return None

    plan_id = plan_data.get("plan_id")
    plan_name = plan_data.get("plan_name", "")
    tier = plan_data.get("tier", "")
    is_free_fallback = False

    # Pull notes off the active sub to mark the auto-downgrade case so
    # the FE can render "Plan cancelled, you're on Free" rather than
    # "Welcome to Free!".
    if plan_name == FREE_PLAN_NAME:
        sub_doc = await db["subscriptions"].find_one(
            {
                "tenant_id": tenant_id,
                "status": {"$in": ["active", "trialing"]},
            },
            projection={"admin_notes": 1},
        )
        if sub_doc and (sub_doc.get("admin_notes") or "").startswith("Auto-downgraded"):
            is_free_fallback = True

    display_name: Optional[str] = plan_data.get("plan_display_name")
    base_price_monthly: Optional[float] = None
    base_price_yearly: Optional[float] = None
    currency: Optional[str] = plan_data.get("currency")

    if plan_id and ObjectId.is_valid(plan_id):
        plan = await get_plan({"_id": ObjectId(plan_id)})
        if plan is not None:
            display_name = plan.display_name or display_name
            base_price_monthly = plan.base_price_monthly
            base_price_yearly = plan.base_price_yearly
            currency = plan.currency or currency

    return {
        "id": plan_id,
        "name": plan_name,
        "displayName": display_name,
        "tier": tier,
        "isFreeFallback": is_free_fallback,
        "subscriptionStatus": plan_data.get("subscription_status"),
        "currentPeriodEnd": plan_data.get("current_period_end"),
        "billingCycle": plan_data.get("billing_cycle"),
        "effectivePrice": plan_data.get("effective_price"),
        "basePriceMonthly": base_price_monthly,
        "basePriceYearly": base_price_yearly,
        "currency": currency,
    }


async def build_me_limitations(
    principal: AuthPrincipal,
) -> Dict[str, Any]:
    """Construct the limitations payload for the calling principal.

    For application admin / application user (no tenant), returns a
    minimal structure with all caps null so the FE knows the platform
    surface is unrestricted by tenant plan.
    """
    tenant_id = principal.tenant_id
    if not tenant_id:
        return {
            "tenantId": None,
            "plan": None,
            "caps": {},
            "deniedEndpoints": [],
            "deniedFeatures": [],
            "lockedEntities": {"branches": [], "departments": []},
            "enterprise": {"isEnterprise": False, "subAppPrefix": None},
            "activeAddons": [],
        }

    plan_data = await resolve_tenant_plan(tenant_id)

    if not plan_data:
        # Should never happen post-bootstrap; the auto-provisioning
        # middleware will have created a Free sub on the next request.
        return {
            "tenantId": tenant_id,
            "plan": None,
            "caps": {},
            "deniedEndpoints": [],
            "deniedFeatures": [],
            "lockedEntities": {"branches": [], "departments": []},
            "enterprise": {"isEnterprise": False, "subAppPrefix": None},
            "activeAddons": [],
        }

    denied_endpoints = _denied_endpoints_from_plan(plan_data)
    denied_features = _denied_feature_keys(denied_endpoints)
    # Tier-level extras: feature keys that gate sub-sections of shared
    # endpoints (not whole endpoints). The matching server-side
    # enforcement lives in each shared endpoint's writer.
    tier_extras = _EXTRA_FEATURE_KEYS_BY_TIER.get(
        str(plan_data.get("tier") or "").lower(), ()
    )
    if tier_extras:
        denied_features = sorted(set(denied_features) | set(tier_extras))

    caps_raw = plan_data.get("tenant_caps") or {}
    # camelCase the cap keys so the FE doesn't have to convert
    caps_out: Dict[str, Any] = {
        "maxBranches": caps_raw.get("max_branches"),
        "maxDepartments": caps_raw.get("max_departments"),
        "maxSystemUsers": caps_raw.get("max_system_users"),
        "maxVisitorsPerMonth": caps_raw.get("max_visitors_per_month"),
        "maxAppointmentsPerMonth": caps_raw.get("max_appointments_per_month"),
    }
    # Analytics ceilings (Insights page). Absent on paid tiers => unlimited.
    caps_out.update(
        _ANALYTICS_CAPS_BY_TIER.get(str(plan_data.get("tier") or "").lower(), {})
    )

    locked_branches = await _list_locked_branch_ids(tenant_id)
    locked_departments = await _list_locked_department_ids(tenant_id)

    tier = plan_data.get("tier", "")
    plan_name = plan_data.get("plan_name", "")
    is_enterprise = tier == "enterprise"
    sub_app_prefix = (
        f"/v1/enterprise/{plan_name}" if is_enterprise and plan_name else None
    )

    return {
        "tenantId": tenant_id,
        "plan": await _build_plan_block(tenant_id, plan_data),
        "caps": caps_out,
        "deniedEndpoints": denied_endpoints,
        "deniedFeatures": denied_features,
        "lockedEntities": {
            "branches": locked_branches,
            "departments": locked_departments,
        },
        "enterprise": {
            "isEnterprise": is_enterprise,
            "subAppPrefix": sub_app_prefix,
        },
        # Addon-inclusive already: the resolved plan snapshot has branch/
        # visitor addon benefits folded into it (see plan_cache_service.
        # _apply_addon_benefits). This is just the raw summary list for
        # display purposes.
        "activeAddons": plan_data.get("active_addons") or [],
    }
