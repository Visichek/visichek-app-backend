from __future__ import annotations

from typing import Protocol

from core.storage.types import StoredObjectInfo, UploadIntent


class DocumentStorageProvider(Protocol):
    """Storage backend contract.

    Client uploads are presign-only: the service picks the ``object_key``
    (so purpose/tenant prefixes are preserved), the provider signs a PUT
    URL for it via :meth:`presign_put`, the client uploads the bytes
    directly, and the service reads the authoritative size/type back with
    :meth:`head_object` at confirm time.

    :meth:`upload_bytes` / :meth:`download_bytes` remain for SERVER-GENERATED
    artifacts only (invoice PDFs, composed visit badges, ID crops) where the
    bytes originate on the server and there is no client to presign for. They
    are NOT used by any client-facing upload route.
    """

    backend_name: str

    def presign_put(
        self, *, object_key: str, mime_type: str, expires_in: int = 3600
    ) -> UploadIntent:
        """Return an UploadIntent the client uses to PUT bytes directly.

        ``object_key`` is chosen by the caller (service layer) so the
        purpose/tenant prefix is preserved. For S3 the signature is bound to
        ``mime_type`` (the client MUST send a matching Content-Type). For the
        local backend the URL points at the in-process upload shim.
        """
        ...

    def head_object(self, *, object_key: str) -> StoredObjectInfo | None:
        """Authoritative size/content-type, or None if the object is absent."""
        ...

    def download_url(self, *, object_key: str, expires_in: int = 900) -> str: ...

    def upload_bytes(
        self, *, object_key: str, payload: bytes, mime_type: str
    ) -> None:
        """SERVER-GENERATED artifacts only — never client uploads."""
        ...

    async def download_bytes(self, object_key: str) -> bytes: ...

    def delete_object(self, *, object_key: str) -> None: ...
