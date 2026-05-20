from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.visitor_profile_repo import (
    create_visitor_profile,
    get_visitor_profile,
    get_visitor_profile_by_phone,
    get_visitor_profile_by_email,
    get_visitor_profile_by_id_number,
    get_visitor_profiles,
    update_visitor_profile,
    soft_delete_visitor_profile,
    search_visitor_profiles,
)
from schemas.visitor_profile_schema import (
    VisitorProfileCreate,
    VisitorProfileUpdate,
    VisitorProfileOut,
    VisitorProfileWithSummaryOut,
)


async def get_or_create_visitor_profile(
    tenant_id: str,
    phone: str | None = None,
    full_name: str = "Unknown",
    company: str | None = None,
    photo_object_key: str | None = None,
    email: str | None = None,
    id_number: str | None = None,
) -> VisitorProfileOut:
    """Find an existing visitor profile or create a new one.

    Lookup order is phone → email → id_number, with phone as the
    canonical identity key per tenant. The MongoDB sparse-unique index
    on ``(tenant_id, phone)`` enforces this at the database layer, so a
    racing concurrent submit will fail the insert; the helper retries
    the phone lookup so the second writer reuses the first writer's row.
    """
    if phone:
        existing = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
        if existing:
            return existing
    if email:
        existing = await get_visitor_profile_by_email(tenant_id=tenant_id, email=email)
        if existing:
            return existing
    if id_number:
        existing = await get_visitor_profile_by_id_number(
            tenant_id=tenant_id, id_number=id_number
        )
        if existing:
            return existing

    profile = VisitorProfileCreate(
        tenant_id=tenant_id,
        phone=phone,
        full_name=full_name,
        company=company,
        photo_object_key=photo_object_key,
        email_address=email,
        id_number=id_number,
    )
    try:
        return await create_visitor_profile(profile)
    except Exception as exc:
        # Phone uniqueness collision: another concurrent writer just
        # created the profile we wanted. Re-fetch by phone and surface
        # that record. Anything else (no phone supplied, or a different
        # error) re-raises.
        if not phone:
            raise
        msg = str(exc).lower()
        if "duplicate key" not in msg and "e11000" not in msg:
            raise
        retry = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
        if retry is not None:
            return retry
        raise


async def retrieve_visitor_profile_by_id(
    profile_id: str, tenant_id: str
) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await get_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Visitor profile not found")
    return result


async def retrieve_visitor_profiles(
    tenant_id: str, start=0, stop=100
) -> List[VisitorProfileOut]:
    return await get_visitor_profiles(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def search_profiles(
    tenant_id: str, query: str, start=0, stop=20
) -> List[VisitorProfileOut]:
    return await search_visitor_profiles(
        tenant_id=tenant_id, query=query, start=start, stop=stop
    )


async def update_profile_by_id(
    profile_id: str, tenant_id: str, profile_data: VisitorProfileUpdate
) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await update_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}, profile_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Visitor profile not found or update failed"
        )
    return result


async def _enrich_visitor_profile(
    profile: VisitorProfileOut,
) -> VisitorProfileWithSummaryOut:
    from services.summary_resolver import resolve_tenant_summary

    tenant_summary = await resolve_tenant_summary(profile.tenant_id)
    data = profile.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_summary
    return VisitorProfileWithSummaryOut(**data)


async def retrieve_visitor_profiles_with_summary(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[VisitorProfileWithSummaryOut]:
    import asyncio

    profiles = await retrieve_visitor_profiles(
        tenant_id=tenant_id, start=start, stop=stop
    )
    return list(await asyncio.gather(*[_enrich_visitor_profile(p) for p in profiles]))


async def retrieve_visitor_profile_by_id_with_summary(
    profile_id: str, tenant_id: str
) -> VisitorProfileWithSummaryOut:
    profile = await retrieve_visitor_profile_by_id(
        profile_id=profile_id, tenant_id=tenant_id
    )
    return await _enrich_visitor_profile(profile)


async def soft_delete_profile(profile_id: str, tenant_id: str) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await soft_delete_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Visitor profile not found")
    return result
