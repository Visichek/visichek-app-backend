"""Host service — business logic for tenant appointment hosts.

A host is a person a visitor can be scheduled to see. It is either backed by
an existing tenant ``system_user`` (``source_system_user_id`` set) or a
*dedicated* host that exists only as a host record. Either way the host's
contact details are snapshotted on the record so reads never need to follow
the link.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.host_repo import (
    count_hosts,
    create_host,
    delete_host,
    get_host,
    get_hosts,
    update_host,
)
from schemas.host_schema import (
    HostCreate,
    HostOut,
    HostUpdate,
    HostWithSummaryOut,
)


def _name_match_filter(name: str) -> dict:
    # Case-insensitive exact match on a trimmed name. re.escape keeps regex
    # metacharacters in user input literal.
    return {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}


async def _assert_department_in_tenant(tenant_id: str, department_id: str) -> None:
    """Reject a host whose department doesn't belong to the tenant."""
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    from repositories.department_repo import get_department

    dept = await get_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}
    )
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found for this tenant",
        )


async def _assert_system_user_in_tenant(tenant_id: str, user_id: str) -> None:
    """Reject a host linked to a system user that isn't in the tenant."""
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid system user ID format")
    from repositories.system_user_repo import get_system_user

    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked system user not found for this tenant",
        )


async def validate_host_create(
    *,
    tenant_id: str,
    name: str,
    phone: str,
    department_id: str,
    source_system_user_id: Optional[str] = None,
) -> None:
    """Synchronous pre-flight check used as the route-level gate.

    Raises before a write is enqueued so the client gets an immediate 4xx
    instead of a 202 followed by a failed-job notification.
    """
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Host name is required")
    if not phone or not phone.strip():
        raise HTTPException(status_code=400, detail="Host phone number is required")

    await _assert_department_in_tenant(tenant_id, department_id)
    if source_system_user_id:
        await _assert_system_user_in_tenant(tenant_id, source_system_user_id)

    # Guard against an obvious duplicate (same name within the tenant).
    existing = await get_host(
        {"tenant_id": tenant_id, "name": _name_match_filter(name)}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A host with this name already exists for this tenant",
        )


async def validate_host_update(
    *,
    host_id: str,
    tenant_id: str,
    name: Optional[str],
    department_id: Optional[str],
) -> None:
    """Pre-flight check for renames / department moves; excludes the host itself."""
    if not ObjectId.is_valid(host_id):
        raise HTTPException(status_code=400, detail="Invalid host ID format")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    if department_id:
        await _assert_department_in_tenant(tenant_id, department_id)

    if name and name.strip():
        existing = await get_host(
            {
                "tenant_id": tenant_id,
                "name": _name_match_filter(name),
                "_id": {"$ne": ObjectId(host_id)},
            }
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A host with this name already exists for this tenant",
            )


async def resolve_host_identity(
    tenant_id: str, host_id: Optional[str]
) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Resolve a host reference to ``(display_name, source_system_user_id)``.

    ``host_id`` may point at the ``hosts`` collection (modern) or — for
    appointments created before the host rewire — a ``system_users`` row.
    Tries the hosts collection first, then system_users, both scoped to the
    tenant. Returns ``None`` when neither resolves.

    Used to snapshot ``host_name_snapshot`` at appointment create and to
    derive the host name during appointment-driven check-in without forcing
    a host to be a login-capable system user. ``source_system_user_id`` is
    the system user (if any) that in-app notifications should target — it is
    ``None`` for a dedicated host, signalling callers to fall back to email
    or skip the in-app notification.
    """
    if not host_id or not ObjectId.is_valid(host_id):
        return None
    host = await get_host({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if host is not None:
        return host.name, host.source_system_user_id
    from repositories.system_user_repo import get_system_user

    user = await get_system_user({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if user is not None:
        # Legacy: the host_id IS a system user.
        return user.full_name, str(user.id or "")
    return None


async def add_host(
    host_data: HostCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> HostOut:
    await validate_host_create(
        tenant_id=host_data.tenant_id,
        name=host_data.name,
        phone=host_data.phone,
        department_id=host_data.department_id,
        source_system_user_id=host_data.source_system_user_id,
    )
    return await create_host(host_data, preassigned_id=preassigned_id)


async def retrieve_host_by_id(host_id: str, tenant_id: str) -> HostOut:
    if not ObjectId.is_valid(host_id):
        raise HTTPException(status_code=400, detail="Invalid host ID format")
    result = await get_host({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Host not found")
    return result


async def retrieve_hosts(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[HostOut]:
    return await get_hosts(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def count_tenant_hosts(tenant_id: str) -> int:
    return await count_hosts({"tenant_id": tenant_id})


def _resolve_stored_image_ref(ref: Optional[str]) -> Optional[str]:
    """Turn a stored image reference into a directly-renderable URL.

    Newly-saved hosts store a storage *object key* (e.g.
    ``system/shared/abc.png``); the upload pipeline persists the key, not the
    expiring ``download_url``. Resolve it to a fresh presigned URL on read via
    the storage provider — mirroring ``branding_service._resolve_logo_urls``.

    Legacy safety: hosts created before the upload migration stored a full
    ``http(s)://…`` URL or a leading-slash path. Those are already usable, so
    return them untouched instead of treating them as object keys.

    Best-effort: returns ``None`` if storage is unconfigured or resolution
    fails, so a storage blip never breaks a host read.
    """
    if not ref:
        return None
    # Legacy values are already presentable — pass through unchanged.
    if ref.startswith(("http://", "https://", "/")):
        return ref
    from services.storage_url_service import try_resolve_download_url

    return try_resolve_download_url(ref)


async def _enrich_host(host: HostOut) -> HostWithSummaryOut:
    from services.summary_resolver import (
        resolve_department_summary,
        resolve_system_user_summary,
        resolve_tenant_summary,
    )

    tenant_s, dept_s, user_s = await asyncio.gather(
        resolve_tenant_summary(host.tenant_id),
        resolve_department_summary(host.department_id),
        resolve_system_user_summary(host.source_system_user_id),
    )
    data = host.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_s
    data["department_summary"] = dept_s
    data["source_system_user_summary"] = user_s
    # Resolve stored object keys to presigned URLs the frontend can drop
    # straight into an <img src> — no client-side /v1/documents round-trip.
    data["picture_url"] = _resolve_stored_image_ref(host.picture_image_url)
    data["signature_url"] = _resolve_stored_image_ref(host.signature_image_url)
    return HostWithSummaryOut(**data)


async def retrieve_hosts_with_summary(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[HostWithSummaryOut]:
    hosts = await retrieve_hosts(tenant_id=tenant_id, start=start, stop=stop)
    return list(await asyncio.gather(*[_enrich_host(h) for h in hosts]))


async def retrieve_host_by_id_with_summary(
    host_id: str, tenant_id: str
) -> HostWithSummaryOut:
    host = await retrieve_host_by_id(host_id=host_id, tenant_id=tenant_id)
    return await _enrich_host(host)


_AUDITABLE_UPDATE_FIELDS = (
    "name",
    "phone",
    "email",
    "department_id",
    "picture_image_url",
    "signature_image_url",
    "is_active",
)


def _diff_host(before: HostOut, after: HostOut) -> Dict[str, Dict[str, Any]]:
    """Return a {field: {before, after}} diff for audit-log details."""
    changes: Dict[str, Dict[str, Any]] = {}
    for field in _AUDITABLE_UPDATE_FIELDS:
        old_val = getattr(before, field, None)
        new_val = getattr(after, field, None)
        if old_val != new_val:
            changes[field] = {"before": old_val, "after": new_val}
    return changes


async def update_host_by_id_with_diff(
    host_id: str,
    tenant_id: str,
    host_data: HostUpdate,
) -> Tuple[HostOut, HostOut, Dict[str, Dict[str, Any]]]:
    """Apply a partial update and return ``(before, after, changes)``."""
    await validate_host_update(
        host_id=host_id,
        tenant_id=tenant_id,
        name=host_data.name,
        department_id=host_data.department_id,
    )
    existing = await get_host({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await update_host(
        {"_id": ObjectId(host_id), "tenant_id": tenant_id}, host_data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Host not found or update failed")
    return existing, result, _diff_host(existing, result)


async def update_host_by_id(
    host_id: str, tenant_id: str, host_data: HostUpdate
) -> HostOut:
    _, after, _ = await update_host_by_id_with_diff(
        host_id=host_id, tenant_id=tenant_id, host_data=host_data
    )
    return after


async def remove_host(host_id: str, tenant_id: str) -> None:
    if not ObjectId.is_valid(host_id):
        raise HTTPException(status_code=400, detail="Invalid host ID format")
    result = await delete_host({"_id": ObjectId(host_id), "tenant_id": tenant_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Host not found")
