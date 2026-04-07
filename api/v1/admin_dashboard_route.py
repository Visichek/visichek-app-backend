from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from security.account_status_check import check_admin_account_status_and_permissions
from services.admin_dashboard_service import get_admin_dashboard_stats

router = APIRouter(prefix="/admins/dashboard", tags=["Admin Dashboard"])


@router.get("/stats")
@document_response(
    message="Admin dashboard stats fetched successfully",
    success_example={
        "total_tenants": 42,
        "active_tenants": 38,
        "total_system_users": 256,
        "total_legacy_users": 5,
        "total_subscriptions": 38,
        "subscription_breakdown": {
            "active": 30,
            "trialing": 5,
            "past_due": 1,
            "cancelled": 2,
            "suspended": 0,
            "expired": 0,
        },
        "total_plans": 6,
        "active_plans": 4,
        "archived_plans": 1,
        "draft_plans": 1,
        "plan_distribution": [
            {
                "plan_id": "507f1f77bcf86cd799439011",
                "plan_name": "Professional",
                "plan_tier": "professional",
                "subscriber_count": 20,
            }
        ],
        "total_incidents": 15,
        "open_incidents": 3,
        "top_tenants_by_incidents": [
            {
                "tenant_id": "507f1f77bcf86cd799439012",
                "company_name": "Acme Corp",
                "incident_count": 5,
            }
        ],
        "total_visitors_all_time": 12500,
        "visitors_this_month": 1800,
        "top_tenants_by_visitors": [
            {
                "tenant_id": "507f1f77bcf86cd799439013",
                "company_name": "MegaCorp",
                "visitor_count": 3200,
            }
        ],
        "total_monthly_revenue": 4999.50,
        "total_yearly_revenue": 12000.00,
        "recent_signups_30d": 7,
        "last_updated": 1712548800,
    },
    description=(
        "Platform-wide statistics for the legacy admin dashboard. "
        "Includes tenant counts, subscription breakdowns, plan distribution, "
        "incident logs, visitor metrics, revenue, and top tenants."
    ),
    summary="Get admin platform-wide stats",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
    },
)
async def admin_dashboard_stats(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    """Platform-wide dashboard for legacy admins."""
    return await get_admin_dashboard_stats()
