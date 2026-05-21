from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from core.storage.provider import DocumentStorageProvider
from core.storage.types import (
    StorageBackend,
    StoredObjectInfo,
    UploadIntent,
)


class LocalStorageProvider(DocumentStorageProvider):
    backend_name = StorageBackend.LOCAL.value

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def presign_put(
        self, *, object_key: str, mime_type: str, expires_in: int = 3600
    ) -> UploadIntent:
        # The local backend can't sign anything; the "upload URL" is the
        # in-process shim that receives a raw PUT body. The 2-step contract
        # (intent -> PUT url -> confirm) stays identical to S3 so the
        # frontend code is uniform across backends.
        return UploadIntent(
            object_key=object_key,
            upload_url=f"/v1/documents/upload-local/{quote(object_key, safe='')}",
            expires_in=expires_in,
            method="PUT",
            headers={"Content-Type": mime_type},
        )

    def head_object(self, *, object_key: str) -> StoredObjectInfo | None:
        file_path = self._root / object_key
        if not file_path.exists():
            return None
        # Local storage has no recorded content type; confirm falls back to
        # the client-declared mime when content_type is None.
        return StoredObjectInfo(size=file_path.stat().st_size, content_type=None)

    def download_url(self, *, object_key: str, expires_in: int = 900) -> str:
        return f"/v1/documents/local/{quote(object_key, safe='')}"

    def delete_object(self, *, object_key: str) -> None:
        file_path = self._root / object_key
        if file_path.exists():
            file_path.unlink()

    def upload_bytes(self, *, object_key: str, payload: bytes, mime_type: str) -> None:
        self.save_bytes(object_key=object_key, payload=payload)

    async def download_bytes(self, object_key: str) -> bytes:
        return self.read_bytes(object_key=object_key)

    def save_bytes(self, *, object_key: str, payload: bytes) -> int:
        file_path = self._root / object_key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        return file_path.stat().st_size

    def read_bytes(self, *, object_key: str) -> bytes:
        file_path = self._root / object_key
        return file_path.read_bytes()
