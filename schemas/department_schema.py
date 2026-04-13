from schemas.imports import *
from pydantic import Field
import time


class DepartmentBase(BaseModel):
    tenant_id: str
    code: str
    name: str
    is_active: bool = True
    created_by: Optional[str] = None


class DepartmentCreate(BaseModel):
    tenant_id: Optional[str] = None
    code: Optional[str] = None
    name: str
    is_active: bool = True
    created_by: Optional[str] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def auto_generate_code(self):
        if not self.code:
            words = self.name.split()
            initials = "".join(w[0].upper() for w in words if w)
            self.code = f"{initials}-{int(time.time()) % 100000}"
        return self


class DepartmentUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class DepartmentOut(DepartmentBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


from schemas.summary_schema import TenantBriefSummary, UserBriefSummary  # noqa: E402


class DepartmentWithSummaryOut(DepartmentOut):
    """DepartmentOut enriched with snapshots of the entities its IDs reference."""

    tenant_summary: Optional[TenantBriefSummary] = None
    created_by_summary: Optional[UserBriefSummary] = None
