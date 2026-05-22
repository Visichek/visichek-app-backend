"""Document read / delete + the local-backend upload transport.

All client uploads now go through the presigned two-step flow at
``/v1/uploads/*`` (see ``api/v1/upload_route.py``). This module keeps only:

  * ``GET /v1/documents/{id}``    — metadata + a fresh presigned download URL.
  * ``DELETE /v1/documents/{id}`` — remove the record + storage object.
  * ``PUT /v1/documents/upload-local/{object_key}`` — the LOCAL-backend
    presign shim. The local provider can't sign a real URL, so its
    ``presign_put`` points the client here; this endpoint receives the raw
    PUT body and writes it to disk. Hidden from the schema and never used by
    the S3 backend.
  * ``GET /v1/documents/local/{object_key}`` — local-backend read transport
    (the ``download_url`` for the local provider).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from core.errors import auth_permission_denied
from core.response_envelope import document_response
from core.storage.local_provider import LocalStorageProvider
from core.storage.manager import DocumentStorageManager
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.document_service import (
    fetch_document,
    fetch_document_with_summary,
    remove_document,
)

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get("/{document_id}")
@document_response(
    message="Document fetched",
    description="Retrieve a document by ID. Returns document metadata and a presigned download URL.",
    summary="Fetch document",
    response_codes={
        200: "Document retrieved successfully",
        401: "Unauthorized - invalid or missing authentication token",
        403: "Forbidden - user lacks permission to access this document",
        404: "Document not found",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Permission denied",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Document not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
    success_example={
        "document": {
            "id": "66f1234567890abcdef12345",
            "owner_id": "user-123",
            "file_name": "invoice-2026-04-07.pdf",
            "object_key": "documents/user-123/invoice-2026-04-07.pdf",
            "backend": "s3",
            "mime_type": "application/pdf",
            "size": 245678,
            "checksum": "d41d8cd98f00b204e9800998ecf8427e",
            "status": "ready",
            "metadata": {"document_type": "invoice", "invoice_number": "INV-2026-001"},
            "created_at": 1712520000,
            "updated_at": 1712520000,
        },
        "download_url": "https://s3.amazonaws.com/visichek-bucket/documents/user-123/invoice-2026-04-07.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=...",
    },
)
async def get_document(
    document_id: str, principal: AuthPrincipal = Depends(verify_any_token)
):
    try:
        doc, download_url = await fetch_document_with_summary(document_id=document_id)
    except Exception:
        doc = None
        download_url = None

    if doc is not None:
        if doc.owner_id != principal.user_id and not principal.is_admin:
            raise auth_permission_denied("GET:/v1/documents/{document_id}")
        return {"document": doc, "download_url": download_url}

    if ".." in document_id:
        return Response(status_code=400)
    provider = DocumentStorageManager.get_instance().provider
    if not isinstance(provider, LocalStorageProvider):
        return Response(status_code=404)
    try:
        data = provider.read_bytes(object_key=document_id)
    except FileNotFoundError:
        return Response(status_code=404)
    return Response(content=data)


@router.delete("/{document_id}")
@document_response(
    message="Document deleted",
    description="Delete a document by ID. Removes the document record and underlying storage object.",
    summary="Delete document",
    response_codes={
        200: "Document deleted successfully",
        401: "Unauthorized - invalid or missing authentication token",
        403: "Forbidden - user lacks permission to delete this document",
        404: "Document not found",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Permission denied",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Document not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
    success_example={"deleted": True},
)
async def delete_document(
    document_id: str, principal: AuthPrincipal = Depends(verify_any_token)
):
    doc, _download_url = await fetch_document(document_id=document_id)
    if doc.owner_id != principal.user_id and not principal.is_admin:
        raise auth_permission_denied("DELETE:/v1/documents/{document_id}")

    await remove_document(document_id=document_id)
    return {"deleted": True}


@router.put("/upload-local/{object_key:path}", include_in_schema=False)
async def upload_local_document(object_key: str, request: Request):
    """LOCAL-backend presign shim — receives the raw PUT body the client sent
    to the ``upload_url`` returned by ``LocalStorageProvider.presign_put``.
    S3 never routes here (the client PUTs straight to S3)."""
    if ".." in object_key:
        return Response(status_code=400)
    provider = DocumentStorageManager.get_instance().provider
    if not isinstance(provider, LocalStorageProvider):
        return Response(status_code=404)

    payload = await request.body()
    provider.save_bytes(object_key=object_key, payload=payload)
    return Response(status_code=204)


@router.get("/local/{object_key:path}", include_in_schema=False)
async def read_local_document(object_key: str):
    if ".." in object_key:
        return Response(status_code=400)
    provider = DocumentStorageManager.get_instance().provider
    if not isinstance(provider, LocalStorageProvider):
        return Response(status_code=404)

    try:
        data = provider.read_bytes(object_key=object_key)
    except FileNotFoundError:
        return Response(status_code=404)
    return Response(content=data)
