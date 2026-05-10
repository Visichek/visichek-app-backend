"""Per-user saved-filter and column-pref endpoints.

These are deliberately synchronous (not queued) because:
1. They are tiny per-user upserts that already get scoped to a single
   doc by ``(user_id, user_type, resource)``.
2. The queued pipeline buys nothing for self-serve UI prefs and would
   add a polling round-trip the frontend doesn't want.

Security: Every handler uses the authenticated principal's id directly
— callers cannot pass a target user_id, and a system_user reading
``/me/...`` only sees their own record (resource scoping is enforced
in :mod:`services.saved_view_service`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.saved_view_schema import (
    ColumnPrefs,
    ColumnPrefsOut,
    SavedFilterCreate,
    SavedFilterEntry,
    SavedFilterUpdate,
    SavedFiltersOut,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.saved_view_service import (
    add_saved_filter,
    get_column_prefs,
    list_saved_filters,
    remove_saved_filter,
    set_column_prefs,
    update_saved_filter,
)

router = APIRouter(prefix="/me", tags=["User saved views"])


# ─── Saved filters ────────────────────────────────────────────────────


@router.get("/saved-filters")
@document_response(
    message="Saved filters fetched",
    summary="List saved filters for a resource",
    description="Return all saved filter sets for the authenticated user on the given resource.",
)
async def list_my_saved_filters(
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> SavedFiltersOut:
    return await list_saved_filters(
        user_id=principal.user_id, role=principal.role, resource=resource
    )


@router.post("/saved-filters", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Saved filter created",
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved filter",
)
async def create_my_saved_filter(
    payload: SavedFilterCreate,
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> SavedFilterEntry:
    return await add_saved_filter(
        user_id=principal.user_id,
        role=principal.role,
        resource=resource,
        payload=payload,
    )


@router.patch("/saved-filters/{filter_id}")
@document_response(
    message="Saved filter updated",
    summary="Update a saved filter",
)
async def update_my_saved_filter(
    filter_id: str,
    payload: SavedFilterUpdate,
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> SavedFilterEntry:
    return await update_saved_filter(
        user_id=principal.user_id,
        role=principal.role,
        resource=resource,
        filter_id=filter_id,
        payload=payload,
    )


@router.delete("/saved-filters/{filter_id}")
@document_response(
    message="Saved filter deleted",
    summary="Delete a saved filter",
    success_example={"deleted": True},
)
async def delete_my_saved_filter(
    filter_id: str,
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
):
    await remove_saved_filter(
        user_id=principal.user_id,
        role=principal.role,
        resource=resource,
        filter_id=filter_id,
    )
    return {"deleted": True}


# ─── Column prefs ─────────────────────────────────────────────────────


@router.get("/column-prefs")
@document_response(
    message="Column prefs fetched",
    summary="Get column visibility/order for a resource",
)
async def get_my_column_prefs(
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> ColumnPrefsOut:
    return await get_column_prefs(
        user_id=principal.user_id, role=principal.role, resource=resource
    )


@router.put("/column-prefs")
@document_response(
    message="Column prefs saved",
    summary="Set column visibility/order for a resource",
)
async def put_my_column_prefs(
    prefs: ColumnPrefs,
    resource: str = Query(..., min_length=1, max_length=40),
    principal: AuthPrincipal = Depends(verify_any_token),
) -> ColumnPrefsOut:
    return await set_column_prefs(
        user_id=principal.user_id,
        role=principal.role,
        resource=resource,
        prefs=prefs,
    )
