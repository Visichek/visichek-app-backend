"""Cloudflare R2 image upload service.

Ported from ``visichek-blog-backend/services/r2_upload.py``. Differences:

* Reads config from ``core.settings.get_settings()`` instead of
  ``os.environ``, matching the host backend's frozen-dataclass pattern.
* Adds a small in-process client cache so repeated uploads (especially
  from the queued ``media.upload_image`` writer) don't rebuild the
  boto3 client every call.
* The blocking ``put_object`` runs via ``asyncio.to_thread`` so it
  doesn't stall the event loop in either web or celery context.
"""

from __future__ import annotations

import asyncio
import mimetypes
import uuid
from typing import Optional

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status

from core.settings import Settings, get_settings

_R2_CLIENT: Optional[BaseClient] = None


def _config_or_raise() -> Settings:
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_ENDPOINT_URL", settings.r2_endpoint_url),
            ("R2_BUCKET", settings.r2_bucket),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"R2 storage is not configured (missing env vars: {', '.join(missing)}).",
        )
    return settings


def _get_client() -> BaseClient:
    """Build (or reuse) the boto3 S3 client pointed at R2."""
    global _R2_CLIENT
    if _R2_CLIENT is not None:
        return _R2_CLIENT
    settings = _config_or_raise()
    _R2_CLIENT = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4", region_name="auto"),
    )
    return _R2_CLIENT


def _make_key(filename: Optional[str], content_type: Optional[str]) -> str:
    ext = ""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    elif content_type:
        ext = mimetypes.guess_extension(content_type) or ""
    return f"uploads/{uuid.uuid4().hex}{ext}"


def _public_url(key: str) -> str:
    settings = get_settings()
    if not settings.public_base_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PUBLIC_BASE_URL is not configured.",
        )
    return f"{settings.public_base_url}/{key}"


def _put_object_sync(
    key: str, body: bytes, content_type: Optional[str]
) -> None:
    client = _get_client()
    settings = get_settings()
    extra = {"ContentType": content_type} if content_type else {}
    try:
        client.put_object(Bucket=settings.r2_bucket, Key=key, Body=body, **extra)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"R2 upload failed: {exc}",
        )


async def upload_image_service_from_bytes(
    file_bytes: bytes, filename: str, content_type: str
) -> str:
    """Upload raw bytes to R2 and return a public URL."""
    _config_or_raise()  # fail fast if mis-configured
    key = _make_key(filename, content_type)
    await asyncio.to_thread(_put_object_sync, key, file_bytes, content_type)
    return _public_url(key)


async def upload_image_service(file: UploadFile) -> str:
    """Read an ``UploadFile`` and upload it to R2."""
    try:
        file_content = await file.read()
    finally:
        await file.close()
    return await upload_image_service_from_bytes(
        file_content,
        file.filename or "",
        file.content_type or "application/octet-stream",
    )
