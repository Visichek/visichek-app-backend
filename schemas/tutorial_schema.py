# ============================================================================
# TUTORIAL SCHEMA
# ============================================================================
# Per-user progress for a single guided tutorial (see TutorialType /
# TutorialStatus in schemas/imports.py). One record per (user_id,
# tutorial_type, version) so a version bump forces a re-run without
# touching prior completions.
# ============================================================================

from __future__ import annotations

from schemas.imports import *


class TutorialBase(BaseModel):
    """Shared fields for a single user's progress on one tutorial.

    Identity (``user_id`` / ``user_role`` / ``user_type`` / ``tenant_id``) is
    assigned by the service layer from the auth token, never by the client.
    A record is unique on ``(user_id, tutorial_type, version)``.
    """

    user_id: str = Field(description="Account id the progress belongs to.")
    user_role: str = Field(
        description="Role at the time the record was written, e.g. 'receptionist'."
    )
    user_type: UserType = Field(
        description="Account collection: admin | system_user | user."
    )
    tutorial_type: TutorialType = Field(description="Which tutorial this tracks.")
    tutorial_status: TutorialStatus = Field(
        default=TutorialStatus.IN_PROGRESS,
        description="Lifecycle state: idle | in_progress | completed | dismissed.",
    )
    version: int = Field(
        default=1,
        description="Tutorial version. Bump to force a re-run after a redesign; "
        "a new version starts a fresh record and keeps prior completions.",
    )
    tenant_id: Optional[str] = Field(
        default=None, description="Owning tenant; None for application admins."
    )


class TutorialCreate(TutorialBase):
    """Internal creation schema — built by the service layer, not the client."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TutorialUpdate(BaseModel):
    """Partial update applied on a repeat PUT for the same
    (user_id, tutorial_type, version). All fields optional."""

    tutorial_status: Optional[TutorialStatus] = Field(
        default=None, description="New lifecycle state."
    )
    version: Optional[int] = Field(default=None, description="New tutorial version.")
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TutorialProgressRequest(BaseModel):
    """Client-supplied body for ``PUT /v1/tutorials``. The user_id / user_role /
    user_type / tenant_id are derived server-side from the auth token — never
    sent by the client (any such field in the body is ignored)."""

    tutorial_type: TutorialType = Field(
        description="One of the TutorialType values; unknown values 422."
    )
    tutorial_status: TutorialStatus = Field(
        description="Target state: in_progress (start), completed, dismissed, idle."
    )
    version: int = Field(default=1, description="Tutorial version, defaults to 1.")


class TutorialOut(TutorialBase):
    """Response object for both endpoints. Serialized camelCase on the wire by
    CaseConversionMiddleware (userId, tutorialType, …)."""

    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = Field(
        default=None, description="Unix seconds when first recorded."
    )
    last_updated: Optional[int] = Field(
        default=None, description="Unix seconds of the last status change."
    )

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
