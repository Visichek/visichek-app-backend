from __future__ import annotations

from schemas.imports import *


# --- Enums ---


class NotificationType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


# --- Notification ---


class NotificationBase(BaseModel):
    """A notification delivered to a user."""

    title: str
    body: str
    type: NotificationType = NotificationType.INFO
    read: bool = False
    link: Optional[str] = None  # optional deep-link
    # The resource that triggered this notification. When set, reading that
    # resource (detail or in a list) auto-marks this notification as read —
    # see services.notification_service.mark_notifications_read_for_resources.
    resource_type: Optional[str] = None  # e.g. "incident", "appointment", "checkin"
    resource_id: Optional[str] = None


class NotificationCreate(NotificationBase):
    """Internal creation schema — built by the service layer."""

    user_id: str
    user_type: UserType  # "admin" or "system_user"
    tenant_id: Optional[str] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))


class NotificationUpdate(BaseModel):
    """Partial update — mainly for marking as read."""

    read: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class NotificationOut(NotificationBase):
    """Response schema for a notification."""

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: Optional[str] = None
    user_type: Optional[UserType] = None
    tenant_id: Optional[str] = None
    date_created: Optional[int] = None

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


class UnreadCountOut(BaseModel):
    """Unread notification count for badge display."""

    count: int = 0


from schemas.summary_schema import TenantBriefSummary, UserBriefSummary  # noqa: E402


class NotificationWithSummaryOut(NotificationOut):
    """NotificationOut enriched with user and tenant snapshots."""

    user_summary: Optional[UserBriefSummary] = None
    tenant_summary: Optional[TenantBriefSummary] = None


# --- Notification Preferences ---


class NotificationPreferencesBase(BaseModel):
    """Per-user notification channel preferences."""

    email_enabled: bool = True
    email_on_incident: bool = True
    email_on_visitor_check_in: bool = False
    email_on_appointment_reminder: bool = True
    email_on_dsr_received: bool = True
    email_on_subscription_alert: bool = True
    email_on_new_user: bool = False
    email_on_support_case: bool = True

    # Push channel. ``push_enabled`` is the master toggle; the per-event
    # flags mirror the ``email_on_*`` set so a notification gates the same
    # way on both channels. ``push_on_visitor_check_in`` defaults True
    # (unlike email) because a real-time device alert is the whole point of
    # push for front-desk staff. Notifications sent without a preference
    # flag (operational alerts) are gated by ``push_enabled`` alone.
    push_enabled: bool = True
    push_on_incident: bool = True
    push_on_visitor_check_in: bool = True
    push_on_appointment_reminder: bool = True
    push_on_dsr_received: bool = True
    push_on_subscription_alert: bool = True
    push_on_new_user: bool = False
    push_on_support_case: bool = True


class NotificationPreferencesCreate(NotificationPreferencesBase):
    """Internal creation schema."""

    user_id: str
    user_type: UserType
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class NotificationPreferencesUpdate(BaseModel):
    """Full replacement — all fields optional for partial."""

    email_enabled: Optional[bool] = None
    email_on_incident: Optional[bool] = None
    email_on_visitor_check_in: Optional[bool] = None
    email_on_appointment_reminder: Optional[bool] = None
    email_on_dsr_received: Optional[bool] = None
    email_on_subscription_alert: Optional[bool] = None
    email_on_new_user: Optional[bool] = None
    email_on_support_case: Optional[bool] = None
    push_enabled: Optional[bool] = None
    push_on_incident: Optional[bool] = None
    push_on_visitor_check_in: Optional[bool] = None
    push_on_appointment_reminder: Optional[bool] = None
    push_on_dsr_received: Optional[bool] = None
    push_on_subscription_alert: Optional[bool] = None
    push_on_new_user: Optional[bool] = None
    push_on_support_case: Optional[bool] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class NotificationPreferencesOut(NotificationPreferencesBase):
    """Response schema for notification preferences."""

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: Optional[str] = None
    user_type: Optional[UserType] = None
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
