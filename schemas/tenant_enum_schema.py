from __future__ import annotations

import re
import time

from schemas.imports import *


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


class TenantEnumOption(BaseModel):
    """A single picker value for a tenant-configured enum.

    ``value`` is the canonical machine-friendly slug stored on every
    check-in record (e.g. ``meeting``); ``label`` is what the kiosk
    shows the visitor (e.g. ``Meeting``). ``active`` lets a tenant
    deactivate a value without deleting it so historical records
    keep their reference intact.
    """

    value: str
    label: str
    active: bool = True
    description: Optional[str] = None
    sort_order: int = 0

    @model_validator(mode="after")
    def _validate(self):
        if not self.value or not _SLUG_RE.match(self.value):
            raise ValueError(
                "value must be 1-64 chars, lowercase letters, digits, '-' or '_'"
            )
        if not self.label or not self.label.strip():
            raise ValueError("label must not be empty")
        if len(self.label) > 80:
            raise ValueError("label must be 80 characters or fewer")
        return self


class TenantEnumBase(BaseModel):
    """Per-tenant set of values for one ``TenantEnumKind``.

    One row per ``(tenant_id, kind)``. The full set of options is held
    inline rather than across many rows so a kiosk page-load can fetch
    every picker in one Redis hit.
    """

    tenant_id: str
    kind: TenantEnumKind
    options: List[TenantEnumOption] = Field(default_factory=list)
    # Whether the visitor can free-type a value not in the option list.
    # ``True`` keeps the kiosk forgiving (default for purpose-of-visit so
    # office staff don't trip on a missing value); ``False`` forces an
    # explicit pick (enforced for id_type so OCR matches).
    allow_custom: bool = False

    @model_validator(mode="after")
    def _validate(self):
        seen: set[str] = set()
        for opt in self.options:
            if opt.value in seen:
                raise ValueError(f"duplicate enum value '{opt.value}'")
            seen.add(opt.value)
        return self


class TenantEnumCreate(TenantEnumBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantEnumUpdate(BaseModel):
    options: Optional[List[TenantEnumOption]] = None
    allow_custom: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantEnumOut(TenantEnumBase):
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


class TenantEnumPublicOut(BaseModel):
    """Sanitised projection used by the public kiosk endpoint.

    Drops timestamps, ids, and any inactive options the kiosk should not
    surface. The kiosk receives a flat per-kind list keyed by
    ``TenantEnumKind`` value (see :class:`TenantEnumBundleOut`).
    """

    kind: TenantEnumKind
    allow_custom: bool
    options: List[TenantEnumOption]


class TenantEnumBundleOut(BaseModel):
    """Bundle of every enum kind for a tenant, keyed by kind value.

    Returned by ``GET /v1/checkin-configs/{id}/enums`` so the kiosk
    needs a single round trip to fetch every picker it might render.
    Inactive options are filtered out before the bundle is built.
    """

    tenant_id: str
    enums: dict[str, TenantEnumPublicOut]
