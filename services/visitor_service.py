from __future__ import annotations

from typing import Optional

from repositories.visitor_repo import (
    create_visitor,
    find_visitor_by_email_or_phone,
    get_visitor,
    update_visitor,
)
from schemas.visitor_schema import (
    VisitorCreate,
    VisitorLookupResponse,
    VisitorOut,
    VisitorUpdate,
)


async def lookup_visitor(
    tenant_id: str, email: Optional[str] = None, phone: Optional[str] = None
) -> VisitorLookupResponse:
    """Lookup a visitor by email and/or phone.

    At least one of email or phone is required.
    """
    if not email and not phone:
        return VisitorLookupResponse(found=False, visitor=None)

    visitor = await find_visitor_by_email_or_phone(
        tenant_id=tenant_id, email=email, phone=phone
    )

    if visitor:
        return VisitorLookupResponse(found=True, visitor=visitor)
    return VisitorLookupResponse(found=False, visitor=None)


async def upsert_visitor_from_checkin(
    tenant_id: str,
    bio_data: dict,
    id_extraction_id: Optional[str] = None,
    visitor_id: Optional[str] = None,
) -> VisitorOut:
    """Upsert a visitor for check-in.

    If visitor_id is provided, updates the existing visitor.
    If not, creates a new one.

    Verified status: new visitors start unverified unless id_extraction succeeded.
    Never downgrade verified for returning visitors.
    """
    # Determine verified status based on id_extraction
    verified = False
    id_document_id = None
    if id_extraction_id:
        try:
            from repositories.id_extraction_repo import get_id_extraction

            extraction = await get_id_extraction({"_id": id_extraction_id})
            if extraction and extraction.verified:
                verified = True
                id_document_id = extraction.document_id
        except Exception:
            pass  # Gracefully skip id extraction lookup

    if visitor_id:
        # Update existing visitor
        visitor = await get_visitor({"_id": visitor_id, "tenant_id": tenant_id})
        if not visitor:
            raise ValueError(f"Visitor {visitor_id} not found in tenant {tenant_id}")

        # Never downgrade verified status
        if visitor.verified:
            verified = True

        update_data = VisitorUpdate(
            bio_data=bio_data,
            verified=verified,
        )
        if id_document_id:
            update_data.id_document_id = id_document_id

        return await update_visitor(visitor_id, update_data)
    else:
        # Create new visitor from bio_data
        # Extract name, email, phone from bio_data
        full_name = bio_data.get("full_name", "Unknown")
        email = bio_data.get("email")
        phone = bio_data.get("phone")

        create_data = VisitorCreate(
            tenant_id=tenant_id,
            full_name=full_name,
            email=email,
            phone=phone,
            bio_data=bio_data,
            verified=verified,
            id_document_id=id_document_id,
        )
        return await create_visitor(create_data)
