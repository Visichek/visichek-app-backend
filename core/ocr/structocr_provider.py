from __future__ import annotations

import base64
import logging

from core.ocr.provider import OCRProvider
from core.ocr.types import OCRResult

logger = logging.getLogger(__name__)


class StructOCRProvider(OCRProvider):
    def __init__(self, api_key: str, api_url: str | None = None):
        self.api_key = api_key
        self.api_url = api_url or "https://api.structocr.com/v1"

    def provider_name(self) -> str:
        return "structocr"

    async def extract_id(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OCRResult:
        try:
            import httpx

            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/ocr/nin",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "image": image_b64,
                        "mime_type": mime_type,
                    },
                )
                response.raise_for_status()
                data = response.json()

            return OCRResult(
                full_name=data.get("full_name") or data.get("name"),
                id_number=data.get("nin") or data.get("id_number"),
                id_type=data.get("document_type", "NIN"),
                date_of_birth=data.get("date_of_birth"),
                confidence=float(data.get("confidence", 0)),
                raw_data=data,
                success=True,
            )
        except Exception as e:
            logger.error(f"StructOCR extraction failed: {e}")
            return OCRResult(success=False, error=str(e))
