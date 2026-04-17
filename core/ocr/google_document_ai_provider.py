from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from schemas.imports import IDType

logger = logging.getLogger(__name__)


class GoogleDocumentAIProvider:
    """Google Document AI provider for ID extraction.

    Uses Google Document AI API to extract structured data from ID documents.
    Supports passport, driver's license, and national ID documents.
    """

    def __init__(
        self,
        gcp_project_id: str,
        gcp_location: str,
        passport_processor_id: str,
        drivers_license_processor_id: str,
        national_id_processor_id: str,
        access_token: Optional[str] = None,
    ):
        self.gcp_project_id = gcp_project_id
        self.gcp_location = gcp_location
        self.passport_processor_id = passport_processor_id
        self.drivers_license_processor_id = drivers_license_processor_id
        self.national_id_processor_id = national_id_processor_id
        self.access_token = access_token
        self._adc_token: Optional[str] = None

    def _get_processor_id(self, id_type: IDType) -> str:
        """Get the processor ID for the given ID type."""
        if id_type == IDType.PASSPORT:
            return self.passport_processor_id
        elif id_type == IDType.DRIVERS_LICENSE:
            return self.drivers_license_processor_id
        elif id_type == IDType.NATIONAL_ID:
            return self.national_id_processor_id
        else:
            raise ValueError(f"Unsupported ID type: {id_type}")

    async def _get_access_token(self) -> str:
        """Get GCP access token via ADC or env var."""
        if self.access_token:
            return self.access_token

        if self._adc_token:
            return self._adc_token

        try:
            from google.auth import default as google_auth_default
            from google.auth.transport.requests import Request

            credentials, _ = google_auth_default()
            credentials.refresh(Request())
            self._adc_token = credentials.token
            return self._adc_token
        except Exception as e:
            logger.warning(
                f"ADC auth failed (expected in dev): {e}. Falling back to env var."
            )
            if not self.access_token:
                raise RuntimeError(
                    "No GCP access token available. Set GCP_ACCESS_TOKEN env var or configure ADC."
                )
            return self.access_token

    async def extract(
        self, document_bytes: bytes, mime_type: str, id_type: IDType
    ) -> dict:
        """Extract ID data from a document.

        Args:
            document_bytes: Raw document file bytes
            mime_type: MIME type (e.g. "image/jpeg", "application/pdf")
            id_type: Type of ID document

        Returns:
            dict with keys: fields (extracted data), confidence (avg), raw_entity_count (int)
        """
        processor_id = self._get_processor_id(id_type)
        if not processor_id:
            raise RuntimeError(f"Processor ID not configured for {id_type}")

        access_token = await self._get_access_token()
        url = (
            f"https://{self.gcp_location}-documentai.googleapis.com/v1/"
            f"projects/{self.gcp_project_id}/locations/{self.gcp_location}/"
            f"processors/{processor_id}:process"
        )

        # Prepare request
        document_b64 = base64.b64encode(document_bytes).decode("utf-8")
        payload = {
            "rawDocument": {
                "content": document_b64,
                "mimeType": mime_type,
            }
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()

        # Extract entities
        entities = result.get("document", {}).get("entities", [])
        fields = self._normalize_entities(entities, id_type)

        # Compute confidence
        confidences = [e.get("confidence", 0.5) for e in entities]
        confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "fields": fields,
            "confidence": confidence,
            "raw_entity_count": len(entities),
        }

    def _normalize_entities(self, entities: list, id_type: IDType) -> dict:
        """Normalize Document AI entities to standard fields."""
        fields = {}

        # Build a map of entity type -> entity for quick lookup
        entity_map = {e.get("type", ""): e for e in entities}

        # Full name: concat given_names + family_name
        given_names = entity_map.get("given_names", {})
        family_name = entity_map.get("family_name", {})
        given_text = (
            given_names.get("normalizedValue", {}).get("text")
            or given_names.get("mentionText", "")
        )
        family_text = (
            family_name.get("normalizedValue", {}).get("text")
            or family_name.get("mentionText", "")
        )
        if given_text or family_text:
            fields["full_name"] = f"{given_text} {family_text}".strip()

        # Date of birth
        dob = entity_map.get("date_of_birth", {})
        dob_text = (
            dob.get("normalizedValue", {}).get("text") or dob.get("mentionText", "")
        )
        if dob_text:
            fields["date_of_birth"] = dob_text

        # Nationality
        nationality = entity_map.get("nationality", {})
        nat_text = (
            nationality.get("normalizedValue", {}).get("text")
            or nationality.get("mentionText", "")
        )
        if nat_text:
            fields["nationality"] = nat_text

        # Address
        address = entity_map.get("address", {})
        addr_text = (
            address.get("normalizedValue", {}).get("text")
            or address.get("mentionText", "")
        )
        if addr_text:
            fields["address"] = addr_text

        # ID number (document_id)
        doc_id = entity_map.get("document_id", {})
        doc_id_text = (
            doc_id.get("normalizedValue", {}).get("text")
            or doc_id.get("mentionText", "")
        )
        if doc_id_text:
            fields["id_number"] = doc_id_text

        # Portrait: Document AI returns a bounding box, not a URL.
        # TODO: implement portrait extraction and upload to storage
        portrait = entity_map.get("portrait", {})
        if portrait:
            fields["portrait_url"] = None  # Set to None for now

        return fields

    def provider_name(self) -> str:
        return "google_document_ai"
