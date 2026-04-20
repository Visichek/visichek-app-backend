from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, model_validator


class QueueJobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class QueueJobLogBase(BaseModel):
    task_id: str
    task_key: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    tenant_id: Optional[str] = None
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    request_id: Optional[str] = None
    status: QueueJobStatus = QueueJobStatus.QUEUED
    payload_redacted: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class QueueJobLogCreate(QueueJobLogBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class QueueJobLogUpdate(BaseModel):
    status: Optional[QueueJobStatus] = None
    resource_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class QueueJobLogOut(QueueJobLogBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values: Any) -> Any:
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
