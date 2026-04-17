from __future__ import annotations

import time
from schemas.imports import *


class IDExtractionBase(BaseModel):
    document_id: str
    provider: IDExtractionProvider = IDExtractionProvider.GOOGLE_DOCUMENT_AI
    id_type: IDType
    extracted_fields: dict
    confidence: Optional[float] = None
    verified: bool = False


class IDExtractionCreate(IDExtractionBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class IDExtractionOut(IDExtractionBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class IDExtractionRequest(BaseModel):
    document_id: str
    id_type: IDType
    provider: IDExtractionProvider = IDExtractionProvider.GOOGLE_DOCUMENT_AI


class IDExtractionResponse(BaseModel):
    id_extraction_id: str
    document_id: str
    provider: IDExtractionProvider
    id_type: IDType
    extracted_fields: dict
    unmatched_required_fields: list[str]
    confidence: Optional[float] = None
    verified: bool
