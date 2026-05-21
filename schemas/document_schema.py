from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from schemas.imports import ObjectId


# Upload intent / confirm DTOs now live in schemas/upload_schema.py — the
# document upload surface is presigned-only via /v1/uploads/*.


class DocumentCreate(BaseModel):
    owner_id: str
    tenant_id: str | None = None
    file_name: str
    object_key: str
    backend: str
    mime_type: str
    size: int
    checksum: str | None = None
    status: str = "ready"
    metadata: dict[str, Any] | None = None
    created_at: int
    updated_at: int


class DocumentOut(BaseModel):
    id: str | None = Field(default=None, alias="_id")
    owner_id: str
    tenant_id: str | None = None
    file_name: str
    object_key: str
    backend: str
    mime_type: str
    size: int
    checksum: str | None = None
    status: str
    metadata: dict[str, Any] | None = None
    created_at: int
    updated_at: int

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True


from schemas.summary_schema import UserBriefSummary  # noqa: E402


class DocumentWithSummaryOut(DocumentOut):
    """DocumentOut enriched with the owning user's snapshot."""

    owner_summary: UserBriefSummary | None = None
