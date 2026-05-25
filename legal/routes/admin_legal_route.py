"""Admin legal-document routes — ``/v1/legal-documents/*``.

Listing follows the host tables contract (``ListSpec`` + ``parse_list_query``
+ ``run_list``). Mutations route through the queued-write pipeline and return
``202 + job_id``; the unfiltered first admin page is served from the
``legal_documents.list_admin`` precompute cache.

Two endpoints do heavy synchronous I/O and therefore commit inline (still
returning the ``{ id, job_id, status }`` envelope via
``record_inline_completed_write``):

* ``POST /import``        — upload a Word/PDF/text file, convert it to
  BlockNote blocks, store the original privately, and create a draft.
* ``POST /upload-image``  — stream an inline image to storage and return its
  URL for embedding as an ``image`` block.

Auth: application admin via ``check_admin_account_status_and_permissions``
(granted to the ``content_only`` preset — see ``config/role_permissions.py``).
"""

from __future__ import annotations

from typing import Any, List

from bson import ObjectId
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)

from blog.services.r2_upload import upload_media_stream
from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write, record_inline_completed_write
from core.response_envelope import document_response
from legal.schemas.imports import LegalDocType, generate_slug
from legal.schemas.legal_document_schema import (
    LegalDocumentCreate,
    LegalDocumentListRow,
    LegalDocumentOut,
    LegalDocumentPublishRequest,
    LegalDocumentUpdate,
    LegalDocumentVersionOut,
)
from legal.services.legal_conversion_service import (
    convert_upload_to_blocks,
    detect_kind,
)
from legal.services.legal_document_service import (
    add_legal_document,
    retrieve_legal_document_by_id,
    retrieve_version,
    retrieve_versions,
    store_source_file,
)
from schemas.admin_schema import AdminOut
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/legal-documents", tags=["Legal Documents Admin"])

_PRECOMPUTE_TTL = 300
# Cap import uploads. Legal docs are text-heavy but small; 25 MB is generous.
_MAX_IMPORT_BYTES = 25 * 1024 * 1024

_DOC_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in LegalDocType)
_STATUS_VALUES: frozenset[str] = frozenset({"draft", "published", "archived"})


LEGAL_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {
            "date_created",
            "last_updated",
            "published_at",
            "effective_at",
            "title",
            "status",
            "doc_type",
        }
    ),
    default_sort=(("date_created", -1),),
    search_fields=("title", "slug", "summary"),
    filters={
        "status": FilterDef(name="status", multi=True, allowed_values=_STATUS_VALUES),
        "docType": FilterDef(
            name="docType",
            mongo_field="doc_type",
            multi=True,
            allowed_values=_DOC_TYPE_VALUES,
        ),
    },
    facet_fields=frozenset({"status", "doc_type"}),
    range_filters={
        "createdAt": "date_created",
        "updatedAt": "last_updated",
        "publishedAt": "published_at",
    },
)


def _row(doc: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Mongo doc into a compact admin list row (no heavy body)."""
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return LegalDocumentListRow(**doc).model_dump(mode="json", by_alias=True)


def _is_default_admin_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    extra = {k for k in qp.keys() if k not in {"skip", "limit"}}
    if extra:
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(LEGAL_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(LEGAL_LIST_SPEC.default_limit)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Legal documents retrieved",
    description=(
        "Paginated admin list. Supports ``skip``/``limit``, ``sort=field,-field``, "
        "``q`` free-text (title/slug/summary), filters ``status`` and ``docType`` "
        "(both multi), and ``facets=status,doc_type``. The unfiltered first page "
        "is served from the precompute cache."
    ),
    summary="List legal documents",
    include_meta=True,
)
async def list_legal_documents_endpoint(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_admin_listing(request):

        async def _loader():
            cursor = (
                db.legal_documents.find({})
                .sort([("date_created", -1)])
                .skip(0)
                .limit(LEGAL_LIST_SPEC.default_limit)
            )
            items: List[dict[str, Any]] = []
            async for doc in cursor:
                items.append(_row(doc))
            total = await db.legal_documents.count_documents({})
            return {"items": items, "total": total}

        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="legal_documents.list_admin",
            ttl=_PRECOMPUTE_TTL,
            loader=_loader,
        )
        if isinstance(cached, dict):
            items = cached.get("items", []) or []
            total = cached.get("total", len(items))
        elif isinstance(cached, list):
            items = cached
            total = len(items)
        else:
            items, total = [], 0
        return {
            "items": items,
            "meta": {
                "total": total,
                "skip": 0,
                "limit": LEGAL_LIST_SPEC.default_limit,
                "hasMore": total > LEGAL_LIST_SPEC.default_limit,
            },
        }

    query = parse_list_query(request, LEGAL_LIST_SPEC)
    return await run_list(
        collection=db.legal_documents,
        query=query,
        map_doc=_row,
    )


# ---------------------------------------------------------------------------
# Static routes BEFORE dynamic /{document_id} (FastAPI matches by order)
# ---------------------------------------------------------------------------


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Document imported",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Upload a Word (.docx), PDF, or text file. The file is converted to "
        "BlockNote content blocks, the original is stored privately (downloadable "
        "later via a presigned URL), and a DRAFT legal document is created. The "
        "response carries the created ``document``, the converted ``blocks`` for "
        "immediate editor rendering, and any conversion ``warnings``."
    ),
    summary="Import Word/PDF/text → draft (streaming)",
)
async def import_legal_document(
    request: Request,
    title: str = Form(..., description="Display name for the document"),
    doc_type: LegalDocType = Form(
        LegalDocType.other, description="Grouping tag; 'other' for custom docs"
    ),
    file: UploadFile = File(..., description="A .docx, .pdf, or .txt/.md file"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    filename = file.filename or ""
    content_type = (file.content_type or "").lower()
    if detect_kind(filename, content_type) == "unsupported":
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type or filename!r}. "
            "Supported: .docx, .pdf, .txt/.md.",
        )

    try:
        file_bytes = await file.read()
    finally:
        await file.close()

    if len(file_bytes) > _MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {_MAX_IMPORT_BYTES // (1024 * 1024)} MB limit.",
        )

    blocks, warnings = await convert_upload_to_blocks(
        file_bytes, filename, content_type
    )

    slug = generate_slug(title)
    source = await store_source_file(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=content_type or "application/octet-stream",
        slug=slug,
    )
    create = LegalDocumentCreate(
        title=title,
        doc_type=doc_type,
        slug=slug,
        body=blocks,
        source_file=source,
    )
    doc = await add_legal_document(create)

    envelope = await record_inline_completed_write(
        writer_key="legal_document.create",
        payload={"title": title, "docType": doc_type.value, "imported": True},
        resource_type="legal_document",
        resource_id=doc.id,
        result={"id": doc.id, "slug": doc.slug, "status": doc.status.value},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
    return {
        **envelope,
        "document": doc.model_dump(mode="json", by_alias=True),
        "blocks": blocks,
        "warnings": warnings,
    }


@router.post("/upload-image", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Image uploaded",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Stream an inline image to storage and return its public URL. Embed the "
        "returned ``url`` in an ``image`` block in the document body. Returns the "
        "``{ id, job_id, status }`` envelope; the URL is in the job ``result``."
    ),
    summary="Upload inline body image (streaming)",
)
async def upload_legal_image(
    request: Request,
    file: UploadFile = File(..., description="Image file"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {content_type}. Expected an image.",
        )
    filename = file.filename or ""
    try:
        url = await upload_media_stream(file.file, filename, content_type)
    finally:
        await file.close()
    return await record_inline_completed_write(
        writer_key="legal_document.upload_image",
        payload={"filename": filename, "content_type": content_type},
        resource_type="legal_image",
        result={"url": url},
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Legal document creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue creation of a draft legal document. Poll ``GET /v1/jobs/{job_id}`` "
        "for the persisted id."
    ),
    summary="Create legal document (queued)",
)
async def create_legal_document_endpoint(
    payload: LegalDocumentCreate,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    data = payload.model_dump(mode="json")
    data["_actor_id"] = getattr(admin, "id", None)
    data["_actor_role"] = "admin"
    data["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="legal_document.create",
        payload=data,
        resource_type="legal_document",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Single document + working-copy mutations
# ---------------------------------------------------------------------------


@router.get("/{document_id}")
@document_response(
    message="Legal document retrieved",
    description="Full admin view of a legal document (working copy + metadata).",
    summary="Get legal document by id",
)
async def get_legal_document_endpoint(
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> LegalDocumentOut:
    return await retrieve_legal_document_by_id(document_id)


@router.patch("/{document_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Legal document update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a partial update of the working copy / metadata. Editing the body "
        "of a published document marks it as having unpublished changes until the "
        "next publish."
    ),
    summary="Update legal document (queued)",
)
async def update_legal_document_endpoint(
    payload: LegalDocumentUpdate,
    request: Request,
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=400, detail="Invalid legal document id format")
    data = payload.model_dump(mode="json", exclude_none=True)
    data["_actor_id"] = getattr(admin, "id", None)
    data["_actor_role"] = "admin"
    data["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="legal_document.update",
        payload=data,
        resource_type="legal_document",
        resource_id=document_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{document_id}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Legal document deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Queue deletion of a legal document and its version history.",
    summary="Delete legal document (queued)",
)
async def delete_legal_document_endpoint(
    request: Request,
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=400, detail="Invalid legal document id format")
    return await enqueue_write(
        writer_key="legal_document.delete",
        payload={
            "_actor_id": getattr(admin, "id", None),
            "_actor_role": "admin",
            "_request_id": getattr(request.state, "request_id", None),
        },
        resource_type="legal_document",
        resource_id=document_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/{document_id}/publish", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Legal document publish queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a publish: snapshots the current working body into an immutable "
        "version and makes it live on the public site. Optional ``effectiveAt`` "
        "(unix seconds) and ``changeNote``."
    ),
    summary="Publish legal document (queued)",
)
async def publish_legal_document_endpoint(
    request: Request,
    document_id: str = Path(..., description="Legal document id"),
    payload: LegalDocumentPublishRequest | None = Body(default=None),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=400, detail="Invalid legal document id format")
    payload = payload or LegalDocumentPublishRequest()
    data = payload.model_dump(mode="json", exclude_none=True)
    data["_actor_id"] = getattr(admin, "id", None)
    data["_actor_role"] = "admin"
    data["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="legal_document.publish",
        payload=data,
        resource_type="legal_document",
        resource_id=document_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{document_id}/archive", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Legal document archive queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Queue archiving a document (removes it from the public site).",
    summary="Archive legal document (queued)",
)
async def archive_legal_document_endpoint(
    request: Request,
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=400, detail="Invalid legal document id format")
    return await enqueue_write(
        writer_key="legal_document.archive",
        payload={
            "_actor_id": getattr(admin, "id", None),
            "_actor_role": "admin",
            "_request_id": getattr(request.state, "request_id", None),
        },
        resource_type="legal_document",
        resource_id=document_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Version history + source-file download
# ---------------------------------------------------------------------------


@router.get("/{document_id}/versions")
@document_response(
    message="Versions retrieved",
    description="List the immutable published version history (newest first).",
    summary="List legal document versions",
    include_meta=True,
)
async def list_versions_endpoint(
    request: Request,
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    qp = request.query_params
    try:
        skip = int(qp.get("skip", "0"))
        limit = int(qp.get("limit", "50"))
    except ValueError:
        raise HTTPException(status_code=400, detail="skip/limit must be integers")
    limit = max(1, min(limit, 200))
    versions = await retrieve_versions(document_id, skip=skip, limit=limit)
    items = [v.model_dump(mode="json", by_alias=True) for v in versions]
    return {
        "items": items,
        "meta": {"skip": skip, "limit": limit, "count": len(items)},
    }


@router.get("/{document_id}/versions/{version}")
@document_response(
    message="Version retrieved",
    description="Fetch a single immutable published version snapshot.",
    summary="Get a legal document version",
)
async def get_version_endpoint(
    document_id: str = Path(..., description="Legal document id"),
    version: int = Path(..., description="Version number"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> LegalDocumentVersionOut:
    return await retrieve_version(document_id, version)


@router.get("/{document_id}/source")
@document_response(
    message="Source file link retrieved",
    description=(
        "Return a presigned download URL for the original uploaded Word/PDF, plus "
        "its file name and MIME type. 404 if the document was not imported from a "
        "file."
    ),
    summary="Get original source-file download URL",
)
async def get_source_file_endpoint(
    document_id: str = Path(..., description="Legal document id"),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    doc = await retrieve_legal_document_by_id(document_id)
    if not doc.source_file or not doc.source_file_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This document has no stored source file.",
        )
    return {
        "url": doc.source_file_url,
        "fileName": doc.source_file.file_name,
        "mimeType": doc.source_file.mime_type,
        "size": doc.source_file.size,
    }
