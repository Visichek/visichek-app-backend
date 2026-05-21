from __future__ import annotations

from typing import List, Optional

from core.errors import auth_role_mismatch
from repositories.tutorial_repo import (
    create_tutorial,
    get_tutorial,
    get_tutorials,
    update_tutorial,
)
from schemas.imports import TutorialStatus, TutorialType, UserType
from schemas.tutorial_schema import TutorialCreate, TutorialOut, TutorialUpdate
from security.principal import TENANT_USER_ROLES

# ── Shell classification (role-gating) ───────────────────────────────
# Tutorials live in one of three shells. The service refuses to record
# progress on a tutorial that doesn't belong to the caller's shell, so a
# tenant user can never accrue platform-admin tutorial progress and a
# platform admin can never accrue tenant tutorial progress.
#
# IMPORTANT: when you add a new TutorialType, classify it here. Anything
# NOT listed below is treated as a TENANT tutorial (see _TENANT_TUTORIALS).
# tests/unit/test_tutorial.py asserts the three sets partition the enum.

# Platform-admin shell — only the application admin role.
_PLATFORM_TUTORIALS: frozenset[TutorialType] = frozenset(
    {
        TutorialType.ADMIN_DASHBOARD_OVERVIEW,
        TutorialType.TENANT_ONBOARDING_REVIEW,
        TutorialType.TENANT_MANAGEMENT,
        TutorialType.PLANS_SETUP,
        TutorialType.SUBSCRIPTIONS_MANAGEMENT,
        TutorialType.DISCOUNTS_SETUP,
        TutorialType.PAYMENTS_REVIEW,
        TutorialType.MARKETING_TOOLS,
        TutorialType.ADMIN_BILLING_OVERVIEW,
    }
)

# Cross-cutting — any authenticated user, either shell.
_CROSS_CUTTING_TUTORIALS: frozenset[TutorialType] = frozenset(
    {
        TutorialType.GETTING_STARTED,
        TutorialType.NOTIFICATIONS_INTRO,
        TutorialType.DATA_TABLE_BASICS,
        TutorialType.SETTINGS_WALKTHROUGH,
    }
)

# Tenant shell — every remaining tutorial; allowed for any tenant role.
_TENANT_TUTORIALS: frozenset[TutorialType] = (
    frozenset(TutorialType) - _PLATFORM_TUTORIALS - _CROSS_CUTTING_TUTORIALS
)


def _enforce_tutorial_shell(user_role: str, tutorial_type: TutorialType) -> None:
    """Reject progress on a tutorial outside the caller's shell (403)."""
    if tutorial_type in _CROSS_CUTTING_TUTORIALS:
        return
    if tutorial_type in _PLATFORM_TUTORIALS:
        if user_role != "admin":
            raise auth_role_mismatch(required_role="admin", actual_role=user_role)
        return
    if tutorial_type in _TENANT_TUTORIALS:
        if user_role not in TENANT_USER_ROLES:
            raise auth_role_mismatch(required_role="tenant_user", actual_role=user_role)
        return
    # Unreachable while the three sets partition the enum; default-deny so an
    # unclassified new TutorialType fails closed rather than open.
    raise auth_role_mismatch(required_role="classified_tutorial", actual_role=user_role)


async def record_tutorial_progress(
    *,
    user_id: str,
    user_role: str,
    user_type: UserType,
    tutorial_type: TutorialType,
    tutorial_status: TutorialStatus,
    version: int = 1,
    tenant_id: Optional[str] = None,
) -> TutorialOut:
    """Upsert a user's progress for a single tutorial.

    Identity is ``(user_id, tutorial_type, version)`` — a version bump starts a
    fresh row so a redesigned tutorial re-runs without losing the old record.

    Raises 403 ``AUTH_ROLE_MISMATCH`` if the tutorial belongs to a shell the
    caller's role can't act in (platform tutorials → admin only; tenant
    tutorials → tenant roles only; cross-cutting → anyone).
    """
    _enforce_tutorial_shell(user_role, tutorial_type)

    filter_dict = {
        "user_id": user_id,
        "tutorial_type": tutorial_type,
        "version": version,
    }
    existing = await get_tutorial(filter_dict)
    if existing:
        updated = await update_tutorial(
            filter_dict, TutorialUpdate(tutorial_status=tutorial_status)
        )
        return updated or existing

    return await create_tutorial(
        TutorialCreate(
            user_id=user_id,
            user_role=user_role,
            user_type=user_type,
            tutorial_type=tutorial_type,
            tutorial_status=tutorial_status,
            version=version,
            tenant_id=tenant_id,
        )
    )


async def retrieve_tutorial_progress(
    user_id: str, user_type: UserType
) -> List[TutorialOut]:
    """Every tutorial-progress record for the authenticated user."""
    return await get_tutorials({"user_id": user_id, "user_type": user_type})
