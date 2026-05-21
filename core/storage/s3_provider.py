from __future__ import annotations

from core.storage.provider import DocumentStorageProvider
from core.storage.types import (
    StorageBackend,
    StoredObjectInfo,
    UploadIntent,
)


class S3StorageProvider(DocumentStorageProvider):
    backend_name = StorageBackend.S3.value

    def __init__(
        self,
        *,
        bucket_name: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]
        except ModuleNotFoundError as err:
            raise RuntimeError("boto3 is required for S3 storage provider") from err

        self._bucket = bucket_name
        # Pin SigV4 + virtual-hosted addressing so presigned PUT/GET URLs are
        # valid against AWS S3 *and* S3-compatible stores like Cloudflare R2.
        # Explicit creds are passed when configured; otherwise boto3 falls back
        # to its default AWS_* credential chain.
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

    def presign_put(
        self, *, object_key: str, mime_type: str, expires_in: int = 3600
    ) -> UploadIntent:
        url = self._client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "ContentType": mime_type,
            },
            ExpiresIn=expires_in,
        )
        # The client MUST send this exact Content-Type or S3 rejects the PUT
        # (it is part of the signed canonical request).
        return UploadIntent(
            object_key=object_key,
            upload_url=url,
            expires_in=expires_in,
            method="PUT",
            headers={"Content-Type": mime_type},
        )

    def head_object(self, *, object_key: str) -> StoredObjectInfo | None:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=object_key)
        except Exception:
            # Missing key (404), access error, etc. — treat as "not present".
            return None
        return StoredObjectInfo(
            size=int(resp.get("ContentLength", 0)),
            content_type=resp.get("ContentType"),
        )

    def download_url(self, *, object_key: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )

    def upload_bytes(self, *, object_key: str, payload: bytes, mime_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=object_key, Body=payload, ContentType=mime_type
        )

    async def download_bytes(self, object_key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=object_key)
        return resp["Body"].read()

    def delete_object(self, *, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)
