from __future__ import annotations

from abc import ABC, abstractmethod

from core.ocr.types import OCRResult


class OCRProvider(ABC):
    @abstractmethod
    async def extract_id(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OCRResult:
        """Extract identity data from an ID image."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        ...
