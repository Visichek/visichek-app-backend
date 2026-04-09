from __future__ import annotations

from fastapi import APIRouter, Depends, Form, UploadFile, File
from fastapi.responses import Response

from core.errors import auth_permission_denied
from core.response_envelope import document_response
from core.storage.local_provider import LocalStorageProvider
from core.storage.manager import DocumentStorageManager
from schemas.document_schema import (
    CompleteUploadRequest,
    UploadIntentRequest,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.document_service import (
    complete_upload,
    create_upload_intent,
    direct_upload,
    fetch_document,
    fetch_document_with_summary,
    remove_document,
)

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("")
@document_response(
    message="Document uploaded successfully",
    status_code=201,
    description="Upload a document directly as multipart/form-data. The file is stored immediately and the document record is returned.",
    summary="Upload document",
    response_codes={
        400: "Invalid file",
        401: "Unauthorized - invalid or missing authentication token",
        413: "File exceeds 50 MB limit",
    },
)
async def upload_document(
    file: UploadFile = File(...),
    mime_type: str | None = Form(default=None),
    principal: AuthPrincipal = Depends(verify_any_token),
):
    payload = await file.read()
    resolved_mime = mime_type or file.content_type or "application/octet-stream"
    doc = await direct_upload(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        file_name=file.filename or "upload",
        mime_type=resolved_mime,
        payload=payload,
    )
    return doc


@router.post("/upload-intents")
@document_response(
    message="Upload intent created",
    status_code=201,
    description="Create an upload intent to prepare for document upload. Returns presigned URL and upload credentials.",
    summary="Create document upload intent",
    response_codes={
        400: "Invalid payload - missing required fields or invalid file size",
        401: "Unauthorized - invalid or missing authentication token",
        413: "Payload too large - file exceeds maximum allowed size",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Invalid file size",
            "code": "VALIDATION_FAILED",
        },
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        413: {
            "success": False,
            "message": "File too large",
            "code": "VALIDATION_FAILED",
        },
    },
    success_example={
        "object_key": "documents/user-123/invoice-2026-04-07.pdf",
        "upload_url": "https://s3.amazonaws.com/visichek-bucket/documents/user-123/invoice-2026-04-07.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=...",
        "expires_in": 3600,
        "method": "PUT",
        "headers": {
            "Content-Type": "application/pdf"
        }
    },
)
async def create_document_upload_intent(
    payload: UploadIntentRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    intent = await create_upload_intent(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        payload=payload,
    )
    return {
        "object_key": intent.object_key,
        "upload_url": intent.upload_url,
        "expires_in": intent.expires_in,
        "method": intent.method,
        "headers": intent.headers,
    }


@router.post("/complete")
@document_response(
    message="Upload completed",
    status_code=201,
    description="Complete a document upload by confirming the object has been uploaded. Finalizes the document record.",
    summary="Complete document upload",
    response_codes={
        201: "Document upload completed successfully",
        401: "Unauthorized - invalid or missing authentication token",
        404: "Upload intent not found or expired",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        404: {
            "success": False,
            "message": "Upload intent not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
    success_example={
        "id": "66f1234567890abcdef12345",
        "owner_id": "user-123",
        "file_name": "invoice-2026-04-07.pdf",
        "object_key": "documents/user-123/invoice-2026-04-07.pdf",
        "backend": "s3",
        "mime_type": "application/pdf",
        "size": 245678,
        "checksum": "d41d8cd98f00b204e9800998ecf8427e",
        "status": "ready",
        "metadata": {
            "document_type": "invoice",
            "invoice_number": "INV-2026-001"
        },
        "created_at": 1712520000,
        "updated_at": 1712520000
    },
)
async def complete_document_upload(
    payload: CompleteUploadRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    doc = await complete_upload(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        payload=payload,
    )
    return doc


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
            "metadata": {
                "document_type": "invoice",
                "invoice_number": "INV-2026-001"
            },
            "created_at": 1712520000,
            "updated_at": 1712520000
        },
        "download_url": "https://s3.amazonaws.com/visichek-bucket/documents/user-123/invoice-2026-04-07.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=..."
    },
)
async def get_document(document_id: str, principal: AuthPrincipal = Depends(verify_any_token)):
    doc, download_url = await fetch_document_with_summary(document_id=document_id)
    if doc.owner_id != principal.user_id and not principal.is_admin:
        raise auth_permission_denied("GET:/v1/documents/{document_id}")
    return {"document": doc, "download_url": download_url}


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
    success_example={
        "deleted": True
    },
)
async def delete_document(document_id: str, principal: AuthPrincipal = Depends(verify_any_token)):
    doc, _download_url = await fetch_document(document_id=document_id)
    if doc.owner_id != principal.user_id and not principal.is_admin:
        raise auth_permission_denied("DELETE:/v1/documents/{document_id}")

    await remove_document(document_id=document_id)
    return {"deleted": True}


@router.post("/upload-local/{object_key}", include_in_schema=False)
async def upload_local_document(object_key: str, file: UploadFile = File(...)):
    if ".." in object_key:
        return Response(status_code=400)
    provider = DocumentStorageManager.get_instance().provider
    if not isinstance(provider, LocalStorageProvider):
        return Response(status_code=404)

    payload = await file.read()
    provider.save_bytes(object_key=object_key, payload=payload)
    return Response(status_code=204)


@router.get("/local/{object_key}", include_in_schema=False)
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
