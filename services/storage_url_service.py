"""The single, reusable way to turn a stored ``object_key`` into a fetchable
URL.

Every read path — branding logos, host roster photos/signatures, appointment
attachments, kiosk check-in fields, invoice PDFs, the documents API — calls
:func:`resolve_download_url` instead of reaching into the storage provider
directly. That keeps the presign TTL and per-backend handling (presigned GET
on S3, local read endpoint in dev) in exactly one place.

Deliberately tiny (only depends on ``core.storage``) so any service can import
it without dragging in the upload/quota machinery and risking import cycles.
"""

from __future__ import annotations

from core.storage.manager import DocumentStorageManager

# Default download-URL lifetime. Long enough to render a page; short enough
# that a leaked URL expires. Callers needing a different window pass expires_in.
DEFAULT_DOWNLOAD_TTL_SECONDS = 24 * 3600


def resolve_download_url(
    object_key: str, *, expires_in: int = DEFAULT_DOWNLOAD_TTL_SECONDS
) -> str:
    """Presigned GET URL (S3) or local read URL for ``object_key``."""
    provider = DocumentStorageManager.get_instance().provider
    return provider.download_url(object_key=object_key, expires_in=expires_in)


def try_resolve_download_url(
    object_key: str | None, *, expires_in: int = DEFAULT_DOWNLOAD_TTL_SECONDS
) -> str | None:
    """Best-effort variant: ``None`` for a falsy key or if storage is
    unconfigured / errors. Use on read paths that must not fail just because a
    URL couldn't be minted (branding, host roster, etc.)."""
    if not object_key:
        return None
    try:
        return resolve_download_url(object_key, expires_in=expires_in)
    except Exception:
        return None
