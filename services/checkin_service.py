from __future__ import annotations

import secrets
import time
from typing import Optional

from core.errors import AppException, ErrorCode, resource_not_found
from repositories.checkin_repo import (
    create_checkin,
    get_active_pending_for_visitor,
    get_checkin,
    get_checkins,
    update_checkin,
    count_checkins,
)
from repositories.badge_repo import create_badge
from repositories.checkin_config_repo import get_checkin_config
from schemas.badge_schema import BadgeCreate, BadgePayload
from schemas.checkin_schema import (
    CheckinCreate,
    CheckinConfirmRequest,
    CheckinOut,
    CheckinState,
    CheckinSubmitRequest,
)


async def submit_checkin(
    checkin_config_id: str, req: CheckinSubmitRequest
) -> CheckinOut:
    """Submit a check-in.

    1. Resolve config (error if inactive)
    2. Validate required fields are present
    3. Upsert visitor
    4. Check for existing pending checkin (409 if exists)
    5. Create checkin with state PENDING_APPROVAL
    6. Fire notification
    """
    # Resolve config
    config = await get_checkin_config(
        {"_id": checkin_config_id, "active": True}
    )
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    tenant_id = config.tenant_id

    # Validate required fields
    bio_data_keys = set(req.bio_data.keys())
    tenant_specific_keys = set(req.tenant_specific_data.keys())
    required_field_keys = {f.key for f in config.required_fields}

    available_keys = bio_data_keys | tenant_specific_keys
    missing_fields = required_field_keys - available_keys
    if missing_fields:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing required fields",
            details={"missing_fields": list(missing_fields)},
        )

    # Upsert visitor
    from services.visitor_service import upsert_visitor_from_checkin

    visitor = await upsert_visitor_from_checkin(
        tenant_id=tenant_id,
        bio_data=req.bio_data,
        id_extraction_id=req.id_extraction_id,
        visitor_id=req.visitor_id,
    )

    visitor_id = visitor.id or ""

    # Check for existing pending checkin
    existing = await get_active_pending_for_visitor(tenant_id, visitor_id)
    if existing:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Visitor has a pending check-in already",
            details={"existing_checkin_id": existing.id},
        )

    # Create checkin
    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=req.id_extraction_id,
        tenant_specific_data=req.tenant_specific_data,
        purpose=req.purpose,
        state=CheckinState.PENDING_APPROVAL,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)

    # Fire notification
    try:
        from services.notification_service import notify_checkin_pending_approval

        await notify_checkin_pending_approval(
            tenant_id=tenant_id,
            checkin_id=checkin.id or "",
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            purpose=req.purpose.purpose,
            host_employee_id="",  # TODO: extract from context if available
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to send checkin notification: {e}")

    return checkin


async def list_checkins_for_tenant(
    tenant_id: str, state: Optional[str] = None, skip: int = 0, limit: int = 20
) -> tuple[list[CheckinOut], int]:
    """List check-ins for a tenant with optional state filter."""
    filter_dict = {"tenant_id": tenant_id}
    if state:
        filter_dict["state"] = state

    checkins = await get_checkins(filter_dict, skip=skip, limit=limit)
    total = await count_checkins(filter_dict)
    return checkins, total


async def get_checkin_detail(tenant_id: str, checkin_id: str) -> CheckinOut:
    """Get check-in detail with tenant validation."""
    checkin = await get_checkin({"_id": checkin_id, "tenant_id": tenant_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)
    return checkin


async def confirm_checkin(
    checkin_id: str, principal, req: CheckinConfirmRequest
) -> dict:
    """Confirm a check-in (approve or reject)."""
    from repositories.visitor_repo import get_visitor

    # Fetch checkin
    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    tenant_id = checkin.tenant_id

    if req.action == "approve":
        # Fetch visitor for response payload
        visitor = await get_visitor({"_id": checkin.visitor_id})
        if not visitor:
            raise resource_not_found(
                resource="Visitor", resource_id=checkin.visitor_id
            )

        # Create badge
        now = int(time.time())
        # Set expires_at to end of tenant's local day (for now use UTC end-of-day)
        expires_at = ((now // 86400) + 1) * 86400  # Next midnight UTC

        qr_code_value = secrets.token_urlsafe(24)
        badge_create = BadgeCreate(
            tenant_id=tenant_id,
            checkin_id=checkin_id,
            qr_code_value=qr_code_value,
            issued_at=now,
            expires_at=expires_at,
        )
        badge = await create_badge(badge_create)

        # Update checkin
        update_data = {
            "state": CheckinState.APPROVED,
            "approved_by_user_id": principal.user_id,
            "approved_at": now,
        }
        await update_checkin(checkin_id, update_data)

        # Fire notification
        try:
            from services.notification_service import notify_checkin_approved

            await notify_checkin_approved(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                badge_id=badge.id or "",
                visitor_name=visitor.full_name,
                host_employee_id="",  # TODO: extract from context if available
                approved_by_user_id=principal.user_id,
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to send approval notification: {e}")

        # Build response
        badge_payload = BadgePayload(
            badge_id=badge.id or "",
            qr_code_value=badge.qr_code_value,
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            portrait_url=visitor.portrait_url,
            host_employee_name=None,  # TODO: resolve from context if available
            purpose=checkin.purpose.purpose,
            issued_at=badge.issued_at,
            expires_at=badge.expires_at,
        )

        return {
            "checkin_id": checkin_id,
            "state": CheckinState.APPROVED,
            "badge": badge_payload,
        }

    elif req.action == "reject":
        now = int(time.time())
        # Update checkin
        update_data = {
            "state": CheckinState.REJECTED,
            "rejection_reason": req.notes,
        }
        await update_checkin(checkin_id, update_data)

        # Fire notification
        try:
            from services.notification_service import notify_checkin_rejected

            visitor = await get_visitor({"_id": checkin.visitor_id})
            await notify_checkin_rejected(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                visitor_name=visitor.full_name if visitor else "Unknown",
                host_employee_id="",  # TODO: extract from context if available
                rejected_by_user_id=principal.user_id,
                reason=req.notes or "No reason provided",
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to send rejection notification: {e}")

        return {
            "checkin_id": checkin_id,
            "state": CheckinState.REJECTED,
            "rejected_by_user_id": principal.user_id,
            "rejected_at": now,
            "rejection_reason": req.notes,
        }
    else:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Invalid action: {req.action}. Must be 'approve' or 'reject'",
        )


async def list_checkins_analytics(
    tenant_id: str,
    state: Optional[str] = None,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[CheckinOut], int]:
    """List check-ins for analytics with date range filtering."""
    filter_dict = {"tenant_id": tenant_id}
    if state:
        filter_dict["state"] = state

    # Add date range filter
    if from_ts is not None or to_ts is not None:
        date_filter = {}
        if from_ts is not None:
            date_filter["$gte"] = from_ts
        if to_ts is not None:
            date_filter["$lte"] = to_ts
        if date_filter:
            filter_dict["date_created"] = date_filter

    checkins = await get_checkins(filter_dict, skip=skip, limit=limit)
    total = await count_checkins(filter_dict)
    return checkins, total


