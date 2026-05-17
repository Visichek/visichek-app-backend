"""Media upload service — Cloudflare R2 with a local filesystem fallback.

Both images and videos go through the same path: bytes go to R2 when
the R2 environment variables are set, otherwise they are written to
``{STORAGE_LOCAL_ROOT}/blog-uploads/`` and served via the static-file
mount in ``main.py`` (relative URL ``/blog-uploads/{name}``).

Two upload paths:

* :func:`upload_media_bytes` — caller already has the full payload in
  RAM. Kept for the codepath that decodes a base64 blob off the celery
  queue and for unit tests; do NOT use for new user-facing uploads.
* :func:`upload_media_stream` — caller passes a file-like object
  (typically ``UploadFile.file``) and we stream it to R2 via boto3's
  multipart transfer (~8 MB parts). Memory stays bounded regardless of
  file size — what made the old base64-in-queue path crash on anything
  over a few MB. This is the path the route handlers use today.

The blocking ``put_object`` / file write runs via ``asyncio.to_thread``
so it doesn't stall the event loop in either web or celery context.
"""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import quote

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status

from core.settings import Settings, get_settings

LOCAL_SUBDIR = "blog-uploads"
LOCAL_URL_PREFIX = f"/{LOCAL_SUBDIR}"

_R2_CLIENT: Optional[BaseClient] = None


def _r2_is_configured(settings: Settings) -> bool:
    return all(
        (
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
            settings.r2_endpoint_url,
            settings.r2_bucket,
            settings.public_base_url,
        )
    )


def _get_client(settings: Settings) -> BaseClient:
    """Build (or reuse) the boto3 S3 client pointed at R2."""
    global _R2_CLIENT
    if _R2_CLIENT is not None:
        return _R2_CLIENT
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


def _make_local_name(filename: Optional[str], content_type: Optional[str]) -> str:
    ext = ""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    elif content_type:
        ext = mimetypes.guess_extension(content_type) or ""
    return f"{uuid.uuid4().hex}{ext}"


def _r2_public_url(settings: Settings, key: str) -> str:
    return f"{settings.public_base_url}/{key}"


def _put_object_sync(
    settings: Settings, key: str, body: bytes, content_type: Optional[str]
) -> None:
    client = _get_client(settings)
    extra = {"ContentType": content_type} if content_type else {}
    try:
        client.put_object(Bucket=settings.r2_bucket, Key=key, Body=body, **extra)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"R2 upload failed: {exc}",
        )


def _write_local_sync(root: Path, name: str, body: bytes) -> None:
    target_dir = root / LOCAL_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(body)


async def upload_media_bytes(
    file_bytes: bytes, filename: str, content_type: str
) -> str:
    """Upload raw bytes to R2 (preferred) or local disk (fallback).

    Returns a public URL suitable for serving directly to clients. R2
    URLs are absolute (built from ``PUBLIC_BASE_URL``); local URLs are
    relative (``/blog-uploads/{name}``) — the frontend resolves them
    against the API base.
    """
    settings = get_settings()
    if _r2_is_configured(settings):
        key = _make_key(filename, content_type)
        await asyncio.to_thread(
            _put_object_sync, settings, key, file_bytes, content_type
        )
        return _r2_public_url(settings, key)

    name = _make_local_name(filename, content_type)
    root = Path(settings.storage_local_root)
    await asyncio.to_thread(_write_local_sync, root, name, file_bytes)
    return f"{LOCAL_URL_PREFIX}/{quote(name)}"


async def upload_image_service_from_bytes(
    file_bytes: bytes, filename: str, content_type: str
) -> str:
    """Backwards-compatible alias for :func:`upload_media_bytes`."""
    return await upload_media_bytes(file_bytes, filename, content_type)


async def upload_image_service(file: UploadFile) -> str:
    """Read an ``UploadFile`` and upload it via :func:`upload_media_bytes`."""
    try:
        file_content = await file.read()
    finally:
        await file.close()
    return await upload_media_bytes(
        file_content,
        file.filename or "",
        file.content_type or "application/octet-stream",
    )


# Multipart transfer config: 8 MB threshold + 8 MB parts so the in-flight
# buffer stays small regardless of how large the file is. Concurrency at 4
# is a reasonable default for typical R2 latencies.
_MULTIPART_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * 1024 * 1024,
    multipart_chunksize=8 * 1024 * 1024,
    max_concurrency=4,
    use_threads=True,
)


def _upload_fileobj_sync(
    settings: Settings,
    key: str,
    fileobj: BinaryIO,
    content_type: Optional[str],
) -> None:
    client = _get_client(settings)
    extra = {"ContentType": content_type} if content_type else {}
    try:
        client.upload_fileobj(
            Fileobj=fileobj,
            Bucket=settings.r2_bucket,
            Key=key,
            ExtraArgs=extra,
            Config=_MULTIPART_TRANSFER_CONFIG,
        )
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"R2 upload failed: {exc}",
        )


def _stream_local_sync(root: Path, name: str, fileobj: BinaryIO) -> None:
    target_dir = root / LOCAL_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    with open(target_dir / name, "wb") as dest:
        shutil.copyfileobj(fileobj, dest, length=8 * 1024 * 1024)


async def upload_media_stream(
    fileobj: BinaryIO, filename: str, content_type: str
) -> str:
    """Stream a file-like object to R2 (preferred) or local disk (fallback).

    Caller passes the underlying stream — typically ``UploadFile.file``
    from a FastAPI route. We do NOT buffer the entire payload in memory:
    boto3's multipart transfer reads ~8 MB at a time, ships it to R2 in
    parallel, and the local fallback uses ``shutil.copyfileobj`` with the
    same chunk size. Memory stays bounded regardless of file size, which
    is what makes large uploads work without OOMing the worker.

    Caller is responsible for closing the source ``UploadFile`` (the
    route handler does this in its ``finally`` block).
    """
    settings = get_settings()
    try:
        fileobj.seek(0)
    except (OSError, AttributeError):
        # Some stream types are not seekable — that's fine, the transfer
        # just reads from the current position.
        pass
    if _r2_is_configured(settings):
        key = _make_key(filename, content_type)
        await asyncio.to_thread(
            _upload_fileobj_sync, settings, key, fileobj, content_type
        )
        return _r2_public_url(settings, key)

    name = _make_local_name(filename, content_type)
    root = Path(settings.storage_local_root)
    await asyncio.to_thread(_stream_local_sync, root, name, fileobj)
    return f"{LOCAL_URL_PREFIX}/{quote(name)}"
