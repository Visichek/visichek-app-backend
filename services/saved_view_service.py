"""Saved view (filters + column prefs) service layer."""

from __future__ import annotations

import re
from typing import Optional
from uuid import uuid4

from core.errors import AppException, ErrorCode
from repositories.saved_view_repo import (
    delete_saved_view,
    get_saved_view,
    list_saved_views_for_user,
    upsert_saved_view,
)
from schemas.saved_view_schema import (
    ColumnPrefs,
    ColumnPrefsOut,
    SavedFilterCreate,
    SavedFilterEntry,
    SavedFilterUpdate,
    SavedFiltersOut,
    SavedViewRecord,
)

# Resource slug must look like a route segment we'd render. Defends
# against any future temptation to use it as a Mongo collection name
# elsewhere by guaranteeing it never contains operators or slashes.
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,40}$")
_MAX_FILTERS_PER_RESOURCE = 20


def _validate_resource(resource: str) -> str:
    if not isinstance(resource, str) or not _RESOURCE_RE.match(resource):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid resource slug",
            details={"field": "resource", "value": resource},
        )
    return resource


def _resolve_user_type(role: str) -> str:
    from security.principal import TENANT_USER_ROLES

    return "system_user" if role in TENANT_USER_ROLES else "admin"


# ─── Saved filters ────────────────────────────────────────────────────


async def list_saved_filters(
    *, user_id: str, role: str, resource: str
) -> SavedFiltersOut:
    _validate_resource(resource)
    record = await get_saved_view(
        user_id=user_id,
        user_type=_resolve_user_type(role),
        resource=resource,
    )
    return SavedFiltersOut(
        resource=resource,
        filters=record.saved_filters if record else [],
    )


async def list_all_saved_views(*, user_id: str, role: str) -> dict[str, list[SavedFilterEntry]]:
    user_type = _resolve_user_type(role)
    records = await list_saved_views_for_user(user_id=user_id, user_type=user_type)
    return {r.resource: r.saved_filters for r in records}


async def add_saved_filter(
    *, user_id: str, role: str, resource: str, payload: SavedFilterCreate
) -> SavedFilterEntry:
    _validate_resource(resource)
    user_type = _resolve_user_type(role)
    record = await get_saved_view(
        user_id=user_id, user_type=user_type, resource=resource
    )
    existing_filters: list[SavedFilterEntry] = record.saved_filters if record else []
    if len(existing_filters) >= _MAX_FILTERS_PER_RESOURCE:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Cannot store more than {_MAX_FILTERS_PER_RESOURCE} saved filters per resource",
            details={"code": "SAVED_FILTERS_LIMIT_REACHED"},
        )
    if any(f.name == payload.name for f in existing_filters):
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Saved filter with this name already exists",
            details={"code": "SAVED_FILTER_NAME_TAKEN"},
        )
    entry = SavedFilterEntry(
        id=str(uuid4()),
        name=payload.name,
        filters=payload.filters,
        is_default=payload.is_default,
    )
    if entry.is_default:
        for f in existing_filters:
            f.is_default = False
    new_record = SavedViewRecord(
        user_id=user_id,
        user_type=user_type,
        resource=resource,
        saved_filters=existing_filters + [entry],
        column_prefs=record.column_prefs if record else None,
    )
    await upsert_saved_view(new_record)
    return entry


async def update_saved_filter(
    *,
    user_id: str,
    role: str,
    resource: str,
    filter_id: str,
    payload: SavedFilterUpdate,
) -> SavedFilterEntry:
    _validate_resource(resource)
    user_type = _resolve_user_type(role)
    record = await get_saved_view(
        user_id=user_id, user_type=user_type, resource=resource
    )
    if not record:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Saved filter not found",
            details={"code": "SAVED_FILTER_NOT_FOUND"},
        )
    target: Optional[SavedFilterEntry] = None
    for f in record.saved_filters:
        if f.id == filter_id:
            target = f
            break
    if target is None:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Saved filter not found",
            details={"code": "SAVED_FILTER_NOT_FOUND"},
        )
    if payload.name is not None:
        if any(
            f.name == payload.name and f.id != filter_id for f in record.saved_filters
        ):
            raise AppException(
                status_code=409,
                code=ErrorCode.VALIDATION_FAILED,
                message="Saved filter with this name already exists",
                details={"code": "SAVED_FILTER_NAME_TAKEN"},
            )
        target.name = payload.name
    if payload.filters is not None:
        target.filters = payload.filters
    if payload.is_default is not None:
        if payload.is_default:
            for f in record.saved_filters:
                f.is_default = False
        target.is_default = payload.is_default
    await upsert_saved_view(record)
    return target


async def remove_saved_filter(
    *, user_id: str, role: str, resource: str, filter_id: str
) -> None:
    _validate_resource(resource)
    user_type = _resolve_user_type(role)
    record = await get_saved_view(
        user_id=user_id, user_type=user_type, resource=resource
    )
    if not record:
        return
    new_filters = [f for f in record.saved_filters if f.id != filter_id]
    if len(new_filters) == len(record.saved_filters):
        return
    record.saved_filters = new_filters
    await upsert_saved_view(record)


# ─── Column prefs ─────────────────────────────────────────────────────


async def get_column_prefs(
    *, user_id: str, role: str, resource: str
) -> ColumnPrefsOut:
    _validate_resource(resource)
    record = await get_saved_view(
        user_id=user_id,
        user_type=_resolve_user_type(role),
        resource=resource,
    )
    prefs = record.column_prefs if record and record.column_prefs else ColumnPrefs()
    return ColumnPrefsOut(resource=resource, prefs=prefs)


async def set_column_prefs(
    *, user_id: str, role: str, resource: str, prefs: ColumnPrefs
) -> ColumnPrefsOut:
    _validate_resource(resource)
    user_type = _resolve_user_type(role)
    record = await get_saved_view(
        user_id=user_id, user_type=user_type, resource=resource
    )
    new_record = SavedViewRecord(
        user_id=user_id,
        user_type=user_type,
        resource=resource,
        saved_filters=record.saved_filters if record else [],
        column_prefs=prefs,
    )
    await upsert_saved_view(new_record)
    return ColumnPrefsOut(resource=resource, prefs=prefs)


async def remove_saved_view(*, user_id: str, role: str, resource: str) -> None:
    _validate_resource(resource)
    await delete_saved_view(
        user_id=user_id,
        user_type=_resolve_user_type(role),
        resource=resource,
    )
