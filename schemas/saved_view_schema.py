"""Per-user saved views: filter sets and column preferences.

Both concepts (saved filter named queries and column visibility/order
prefs) live on the same record keyed by ``(user_id, user_type,
resource)`` because the frontend always renders them together in the
same dropdown — sharing storage avoids two separate fetches when a
table mounts.

Security:

* ``resource`` is a slug bound to the route the frontend is rendering
  ("tenants", "system_users", …). It is NOT used to construct any
  Mongo collection name on the read path; it's only an opaque tag the
  client uses to scope its own UI state.
* ``filters`` is opaque JSON the frontend round-trips back into its
  own URL state. The backend never executes it as a Mongo expression
  — that would let any logged-in user smuggle filters to escape
  tenant scoping at query time.
* The size cap on each field bounds Redis / Mongo blast radius if
  someone tries to stuff megabytes of state into the record.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from schemas.imports import (
    BaseModel,
    Field,
    ObjectId,
    model_validator,
    time,
)


_MAX_NAME = 80
_MAX_FILTER_BYTES = 8 * 1024  # 8 KB per saved filter doc
_MAX_COLUMN_PREFS_BYTES = 4 * 1024


def _validate_size(value: Any, max_bytes: int, label: str) -> Any:
    if value is None:
        return value
    serialized = json.dumps(value)
    if len(serialized) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    return value


class SavedFilterEntry(BaseModel):
    """A single named filter set on a resource."""

    id: str
    name: str = Field(..., max_length=_MAX_NAME)
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def _validate(self):
        _validate_size(self.filters, _MAX_FILTER_BYTES, "filters")
        return self


class SavedFilterCreate(BaseModel):
    name: str = Field(..., max_length=_MAX_NAME)
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False

    @model_validator(mode="after")
    def _validate(self):
        _validate_size(self.filters, _MAX_FILTER_BYTES, "filters")
        return self


class SavedFilterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=_MAX_NAME)
    filters: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.filters is not None:
            _validate_size(self.filters, _MAX_FILTER_BYTES, "filters")
        return self


class SavedFiltersOut(BaseModel):
    """All saved filters for one resource."""

    resource: str
    filters: List[SavedFilterEntry] = Field(default_factory=list)


class ColumnPrefs(BaseModel):
    """Per-resource column visibility & order preference."""

    visible: List[str] = Field(default_factory=list)
    order: List[str] = Field(default_factory=list)
    pinned: List[str] = Field(default_factory=list)
    widths: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self):
        _validate_size(self.model_dump(), _MAX_COLUMN_PREFS_BYTES, "column_prefs")
        return self


class ColumnPrefsOut(BaseModel):
    resource: str
    prefs: ColumnPrefs


class SavedViewRecord(BaseModel):
    """Internal record stored in MongoDB."""

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    user_type: str
    resource: str
    saved_filters: List[SavedFilterEntry] = Field(default_factory=list)
    column_prefs: Optional[ColumnPrefs] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

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
