"""FreeImage.Host fallback upload + BlockNote media block helper.

Ported from ``visichek-blog-backend/services/image_host.py``. R2 is the
preferred upload path; FreeImage.Host is retained so legacy callers
still work, but routes default to ``r2_upload.upload_image_service``.
"""

from __future__ import annotations

import uuid
from typing import Literal

import httpx
from fastapi import HTTPException, UploadFile, status

from core.settings import get_settings

FREEIMAGE_API_URL = "https://freeimage.host/api/1/upload"


def _api_key_or_raise() -> str:
    settings = get_settings()
    if not settings.freeimage_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Image hosting service is not configured. API key is missing.",
        )
    return settings.freeimage_api_key


async def upload_to_freeimage_service_from_bytes(
    file_bytes: bytes, filename: str, content_type: str
) -> str:
    api_key = _api_key_or_raise()
    params = {"key": api_key, "action": "upload", "format": "json"}
    files_payload = {"source": (filename, file_bytes, content_type)}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                FREEIMAGE_API_URL, params=params, files=files_payload
            )
            response.raise_for_status()
        except httpx.RequestError as exc:
            raise HTTPException(503, f"Connection failed: {exc}")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                exc.response.status_code,
                f"Image host returned error: {exc.response.text}",
            )

    try:
        data = response.json()
        if data.get("status_code") != 200:
            raise HTTPException(400, f"Host failed: {data.get('status_txt')}")
        return data["image"]["url"]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, f"Invalid host response: {response.text}")


async def upload_to_freeimage_service(file: UploadFile) -> str:
    api_key = _api_key_or_raise()
    try:
        file_content = await file.read()
        files_payload = {"source": (file.filename, file_content, file.content_type)}
    finally:
        await file.close()

    params = {"key": api_key, "action": "upload", "format": "json"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                FREEIMAGE_API_URL, params=params, files=files_payload
            )
            response.raise_for_status()
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to connect to the image hosting service: {exc}",
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Image host returned an error: {exc.response.text}",
            )

    try:
        data = response.json()
        if (
            data.get("status_code") != 200
            or "image" not in data
            or "url" not in data["image"]
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image host failed to process the image: {data.get('status_txt', 'Unknown error')}",
            )
        return str(data["image"]["url"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse response from image host: {response.text}",
        )


def generate_media_json(
    file_url: str,
    caption: str = "",
    media_type: Literal["image", "video"] = "image",
) -> dict:
    """Build a BlockNote media block ready to append to ``currentPageBody``."""
    full_caption = f"📷 {caption}" if caption else "📷"
    return {
        "id": str(uuid.uuid4()),
        "type": media_type,
        "props": {
            "textAlignment": "center",
            "backgroundColor": "default",
            "name": "",
            "url": file_url,
            "caption": full_caption,
            "showPreview": True,
            "previewWidth": 756,
        },
        "children": [],
    }
