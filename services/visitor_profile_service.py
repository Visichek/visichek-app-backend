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
    """Find an existing visitor profile by phone, email, or id_number, or create a new one."""
    # Try phone first
    if phone:
        existing = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
        if existing:
            return existing
    # Try email next
    if email:
        existing = await get_visitor_profile_by_email(tenant_id=tenant_id, email=email)
        if existing:
            return existing
    # Try id_number last
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
    return await create_visitor_profile(profile)


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
