from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.tenant_repo import get_tenant, update_tenant
from repositories.subscription_repo import get_subscription, update_subscription
from repositories.system_user_repo import get_system_users, update_system_user
from schemas.tenant_schema import TenantUpdate
from schemas.subscription_schema import SubscriptionUpdate, SubscriptionStatus
from schemas.system_user_schema import SystemUserUpdate
from schemas.imports import AccountStatus
from services.audit_service import record_audit_event
from services.plan_cache_service import invalidate_tenant_plan_cache

logger = logging.getLogger(__name__)


async def offboard_tenant(
    tenant_id: str,
    reason: str,
    admin_id: str,
) -> Dict[str, Any]:
    """Offboard (deactivate) a tenant and clean up associated resources.

    Steps:
    1. Cancel active subscription (set status=cancelled, cancelled_at=now)
    2. Deactivate all system_users for the tenant (set account_status="inactive")
    3. Mark tenant as inactive (update tenant is_active=False)
    4. Record audit event for offboarding
    5. Invalidate plan cache
    6. Return summary of actions taken

    Args:
        tenant_id: The tenant to offboard
        reason: Reason for offboarding (e.g., "customer_request", "non_payment")
        admin_id: The admin performing the offboarding

    Returns:
        Dict containing summary of actions taken

    Raises:
        HTTPException if tenant not found
    """
    # Verify tenant exists
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    now = int(time.time())
    summary: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "offboarded_at": now,
        "reason": reason,
        "actions": {
            "subscription_cancelled": False,
            "users_deactivated_count": 0,
            "tenant_marked_inactive": False,
        },
    }

    try:
        # Step 1: Cancel active subscription
        active_sub = await get_subscription({
            "tenant_id": tenant_id,
            "status": {"$in": [
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIALING.value,
            ]},
        })
        if active_sub:
            sub_update = SubscriptionUpdate(
                status=SubscriptionStatus.CANCELLED,
                cancelled_at=now,
                cancellation_reason=f"Tenant offboarded: {reason}",
            )
            await update_subscription(
                {"_id": ObjectId(active_sub.id)},
                sub_update,
            )
            summary["actions"]["subscription_cancelled"] = True
            logger.info(
                "Cancelled subscription for tenant %s (reason: %s)",
                tenant_id,
                reason,
            )

        # Step 2: Deactivate all system users for the tenant
        system_users = await get_system_users(
            filter_dict={"tenant_id": tenant_id},
            start=0,
            stop=50000,
        )
        deactivated_count = 0
        for user in system_users:
            if user.account_status != AccountStatus.INACTIVE.value:
                user_update = SystemUserUpdate(
                    account_status=AccountStatus.INACTIVE,
                    last_updated=now,
                )
                await update_system_user(
                    {"_id": ObjectId(user.id)},
                    user_update,
                )
                deactivated_count += 1

        summary["actions"]["users_deactivated_count"] = deactivated_count
        logger.info(
            "Deactivated %d system users for tenant %s",
            deactivated_count,
            tenant_id,
        )

        # Step 3: Mark tenant as inactive
        tenant_update = TenantUpdate(
            is_active=False,
            last_updated=now,
        )
        await update_tenant(
            {"_id": ObjectId(tenant_id)},
            tenant_update,
        )
        summary["actions"]["tenant_marked_inactive"] = True
        logger.info("Marked tenant %s as inactive", tenant_id)

        # Step 4: Record audit event
        await record_audit_event(
            actor_id=admin_id,
            actor_role="admin",
            action="tenant.offboarded",
            resource_type="tenant",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            details={
                "reason": reason,
                "subscription_cancelled": summary["actions"]["subscription_cancelled"],
                "users_deactivated_count": deactivated_count,
            },
        )
        logger.info("Recorded offboarding audit event for tenant %s", tenant_id)

        # Step 5: Invalidate plan cache for the tenant
        await invalidate_tenant_plan_cache(tenant_id)

        return summary

    except Exception as e:
        logger.error(
            "Error offboarding tenant %s: %s",
            tenant_id,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to offboard tenant: {str(e)}",
        )


async def get_offboarding_summary(tenant_id: str) -> Dict[str, Any]:
    """Get current state/summary of a tenant for offboarding assessment.

    Returns data about:
    - Tenant status
    - Subscription status
    - System user count and statuses
    - Data statistics

    Args:
        tenant_id: The tenant to assess

    Returns:
        Dict with current state summary

    Raises:
        HTTPException if tenant not found
    """
    # Verify tenant exists
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Get subscription status
    subscription = await get_subscription({"tenant_id": tenant_id})
    sub_status = subscription.status if subscription else None

    # Count system users and their statuses
    system_users = await get_system_users(
        filter_dict={"tenant_id": tenant_id},
        start=0,
        stop=50000,
    )
    active_users = sum(
        1 for user in system_users
        if user.account_status == AccountStatus.ACTIVE.value
    )
    inactive_users = sum(
        1 for user in system_users
        if user.account_status == AccountStatus.INACTIVE.value
    )
    suspended_users = sum(
        1 for user in system_users
        if user.account_status == AccountStatus.SUSPENDED.value
    )

    return {
        "tenant_id": tenant_id,
        "tenant_active": tenant.is_active,
        "company_name": tenant.company_name,
        "subscription_status": sub_status,
        "system_users": {
            "total": len(system_users),
            "active": active_users,
            "inactive": inactive_users,
            "suspended": suspended_users,
        },
        "assessment_timestamp": int(time.time()),
    }
