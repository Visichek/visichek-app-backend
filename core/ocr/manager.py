from __future__ import annotations

import logging

from core.ocr.provider import OCRProvider
from core.ocr.types import OCRResult

logger = logging.getLogger(__name__)


class OCRManager:
    _instance: OCRManager | None = None
    _provider: OCRProvider | None = None

    @classmethod
    def configure(cls, provider: OCRProvider) -> None:
        if cls._instance is None:
            cls._instance = cls()
        cls._instance._provider = provider
        logger.info("OCRManager configured with provider: %s", provider.provider_name())

    @classmethod
    def configure_from_settings(cls) -> None:
        from core.settings import get_settings

        settings = get_settings()
        provider_name = settings.ocr_provider

        if provider_name == "structocr":
            from core.ocr.structocr_provider import StructOCRProvider

            if not settings.ocr_api_key:
                raise RuntimeError("OCR_API_KEY is required for StructOCR provider")
            provider = StructOCRProvider(
                api_key=settings.ocr_api_key,
                api_url=settings.ocr_api_url,
            )
        else:
            raise RuntimeError(f"Unknown OCR provider: {provider_name}")

        cls.configure(provider)

    @classmethod
    def get_instance(cls) -> OCRManager:
        if cls._instance is None or cls._instance._provider is None:
            raise RuntimeError("OCRManager is not configured. Call configure() or configure_from_settings() first.")
        return cls._instance

    @classmethod
    def is_configured(cls) -> bool:
        return cls._instance is not None and cls._instance._provider is not None

    async def extract_id(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OCRResult:
        if self._provider is None:
            raise RuntimeError("OCR provider not configured")
        return await self._provider.extract_id(image_bytes, mime_type)
