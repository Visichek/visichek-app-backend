"""Media schemas (images + videos).

Ported from ``visichek-blog-backend/schemas/media_host.py`` to Pydantic
v2. ``MediaOutUser`` keeps the http→https URL coercion the public
website relies on.
"""

from __future__ import annotations

import time
from typing import Any, List, Literal, Optional

from bson import ObjectId
from pydantic import AliasChoices, BaseModel, Field, model_validator

from blog.schemas.imports import CategoryNameEnum


class ImageUploadResponse(BaseModel):
    """The ``data`` field for an image upload response."""

    url: str


class VideoUploadResponse(BaseModel):
    """The ``data`` field for a video upload response."""

    url: str


class MediaBase(BaseModel):
    mediaType: Literal["video", "image"]
    category: CategoryNameEnum
    requestUrl: Optional[str] = None


class MediaUpdate(BaseModel):
    category: CategoryNameEnum


class MediaCreate(MediaBase):
    url: str
    name: str
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class MediaOut(MediaCreate):
    totalItems: Optional[int] = None
    itemIndex: Optional[int] = None
    date_created: Optional[int] = Field(  # type: ignore[assignment]
        default=None,
        validation_alias=AliasChoices("date_created", "dateCreated"),
        serialization_alias="dateCreated",
    )
    last_updated: Optional[int] = Field(  # type: ignore[assignment]
        default=None,
        validation_alias=AliasChoices("last_updated", "lastUpdated"),
        serialization_alias="lastUpdated",
    )
    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class MediaOutUser(MediaCreate):
    """Public-website media row. Coerces http → https for string fields."""

    itemIndex: Optional[int] = None
    date_created: Optional[int] = Field(  # type: ignore[assignment]
        default=None,
        validation_alias=AliasChoices("date_created", "dateCreated"),
        serialization_alias="dateCreated",
    )
    last_updated: Optional[int] = Field(  # type: ignore[assignment]
        default=None,
        validation_alias=AliasChoices("last_updated", "lastUpdated"),
        serialization_alias="lastUpdated",
    )
    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )

    @staticmethod
    def _http_to_https(value: str) -> str:
        if isinstance(value, str) and value.startswith("http://"):
            return "https://" + value[len("http://") :]
        return value

    @model_validator(mode="before")
    @classmethod
    def coerce_objectid_and_urls(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        for k, v in list(values.items()):
            if isinstance(v, str):
                values[k] = cls._http_to_https(v)
            elif isinstance(v, list):
                values[k] = [
                    cls._http_to_https(item) if isinstance(item, str) else item
                    for item in v
                ]
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class ListOfMediaOut(BaseModel):
    totalItems: Optional[int] = None
    listOfMedia: List[MediaOutUser]


__all__ = [
    "ImageUploadResponse",
    "VideoUploadResponse",
    "MediaBase",
    "MediaUpdate",
    "MediaCreate",
    "MediaOut",
    "MediaOutUser",
    "ListOfMediaOut",
]
