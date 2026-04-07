from __future__ import annotations

"""
Platform-wide dashboard service for application admins.

Aggregates data across ALL tenants — total users, subscriptions, plan
distribution, incident trends, revenue, and top-performing tenants.
"""

import time
from typing import List

from core.database import db
from schemas.admin_dashboard_schema import (
    AdminDashboardStats,
    PlanDistribution,
    TopTenantByIncidents,
    TopTenantByVisitors,
    SubscriptionStatusBreakdown,
)


async def get_admin_dashboard_stats() -> AdminDashboardStats:
    """Aggregate platform-wide stats for the application admin dashboard."""

    # --- Tenant counts ---
    total_tenants = await db["tenant_companies"].count_documents({})
    active_tenants = await db["tenant_companies"].count_documents({"is_active": True})

    # --- User counts ---
    total_tenant_users = await db["system_users"].count_documents({})
    total_application_users = await db["users"].count_documents({})

    # --- Subscription counts & breakdown ---
    total_subscriptions = await db["subscriptions"].count_documents({})
    breakdown = SubscriptionStatusBreakdown()
    pipeline_sub_status = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    async for doc in db["subscriptions"].aggregate(pipeline_sub_status):
        status_key = doc["_id"]
        if hasattr(breakdown, status_key):
            setattr(breakdown, status_key, doc["count"])

    # --- Plan counts ---
    total_plans = await db["plans"].count_documents({})
    active_plans = await db["plans"].count_documents({"status": "active"})
    archived_plans = await db["plans"].count_documents({"status": "archived"})
    draft_plans = await db["plans"].count_documents({"status": "draft"})

    # --- Plan distribution (subscriptions per plan) ---
    pipeline_plan_dist = [
        {"$match": {"status": {"$in": ["active", "trialing"]}}},
        {"$group": {"_id": "$plan_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    plan_distribution: List[PlanDistribution] = []
    async for doc in db["subscriptions"].aggregate(pipeline_plan_dist):
        plan_id = doc["_id"]
        plan_doc = await db["plans"].find_one({"_id": _safe_objectid(plan_id)})
        plan_name = plan_doc["display_name"] if plan_doc else "Unknown"
        plan_tier = plan_doc.get("tier", "unknown") if plan_doc else "unknown"
        plan_distribution.append(
            PlanDistribution(
                plan_id=str(plan_id),
                plan_name=plan_name,
                plan_tier=plan_tier,
                subscriber_count=doc["count"],
            )
        )

    # --- Incident stats ---
    total_incidents = await db["incidents"].count_documents({})
    open_incidents = await db["incidents"].count_documents(
        {"status": {"$in": ["open", "investigating"]}}
    )

    # Top tenants by incidents
    pipeline_incidents = [
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_by_incidents: List[TopTenantByIncidents] = []
    async for doc in db["incidents"].aggregate(pipeline_incidents):
        tenant_id = doc["_id"]
        tenant_doc = await db["tenant_companies"].find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        company_name = tenant_doc["company_name"] if tenant_doc else "Unknown"
        top_by_incidents.append(
            TopTenantByIncidents(
                tenant_id=str(tenant_id),
                company_name=company_name,
                incident_count=doc["count"],
            )
        )

    # --- Visitor stats ---
    total_visitors_all_time = await db["visitors"].count_documents({})

    now = int(time.time())
    month_start = now - (30 * 24 * 60 * 60)
    visitors_this_month = await db["visitors"].count_documents(
        {"date_created": {"$gte": month_start}}
    )

    # Top tenants by visitors
    pipeline_visitors = [
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_by_visitors: List[TopTenantByVisitors] = []
    async for doc in db["visitors"].aggregate(pipeline_visitors):
        tenant_id = doc["_id"]
        tenant_doc = await db["tenant_companies"].find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        company_name = tenant_doc["company_name"] if tenant_doc else "Unknown"
        top_by_visitors.append(
            TopTenantByVisitors(
                tenant_id=str(tenant_id),
                company_name=company_name,
                visitor_count=doc["count"],
            )
        )

    # --- Revenue (sum of effective_price on active subs) ---
    pipeline_revenue = [
        {"$match": {"status": {"$in": ["active", "trialing"]}}},
        {
            "$group": {
                "_id": "$billing_cycle",
                "total": {"$sum": "$effective_price"},
            }
        },
    ]
    total_monthly_revenue = 0.0
    total_yearly_revenue = 0.0
    async for doc in db["subscriptions"].aggregate(pipeline_revenue):
        if doc["_id"] == "monthly":
            total_monthly_revenue = round(doc["total"], 2)
        elif doc["_id"] == "yearly":
            total_yearly_revenue = round(doc["total"], 2)

    # --- Recent signups (tenants created in last 30 days) ---
    recent_signups_30d = await db["tenant_companies"].count_documents(
        {"date_created": {"$gte": month_start}}
    )

    return AdminDashboardStats(
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_tenant_users=total_tenant_users,
        total_application_users=total_application_users,
        total_subscriptions=total_subscriptions,
        subscription_breakdown=breakdown,
        total_plans=total_plans,
        active_plans=active_plans,
        archived_plans=archived_plans,
        draft_plans=draft_plans,
        plan_distribution=plan_distribution,
        total_incidents=total_incidents,
        open_incidents=open_incidents,
        top_tenants_by_incidents=top_by_incidents,
        total_visitors_all_time=total_visitors_all_time,
        visitors_this_month=visitors_this_month,
        top_tenants_by_visitors=top_by_visitors,
        total_monthly_revenue=total_monthly_revenue,
        total_yearly_revenue=total_yearly_revenue,
        recent_signups_30d=recent_signups_30d,
    )


def _safe_objectid(val):
    """Try to convert to ObjectId, return as-is if already one or invalid."""
    from bson import ObjectId

    if isinstance(val, ObjectId):
        return val
    try:
        return ObjectId(val)
    except Exception:
        return val
