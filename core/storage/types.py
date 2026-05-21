from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StorageBackend(str, Enum):
    LOCAL = "local"
    S3 = "s3"


@dataclass(frozen=True)
class UploadIntent:
    object_key: str
    upload_url: str
    expires_in: int
    method: str = "PUT"
    headers: dict[str, str] | None = None
    form_fields: dict[str, str] | None = None


@dataclass(frozen=True)
class StoredDocument:
    object_key: str
    backend: StorageBackend
    mime_type: str
    size: int
    checksum: str | None
    created_at: str = datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DocumentMetadata:
    owner_id: str
    file_name: str
    mime_type: str
    size: int
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class StoredObjectInfo:
    """Authoritative facts read back from storage AFTER a client upload.

    Returned by ``DocumentStorageProvider.head_object``. Because client
    uploads now go straight to storage via a presigned URL, the server
    never sees the bytes — this HEAD is the only trustworthy source for
    the real size / content type used to enforce storage quota and the
    image-only MIME guard at confirm time.
    """

    size: int
    content_type: str | None = None
