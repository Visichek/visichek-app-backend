from __future__ import annotations

from schemas.imports import *


# --- Enums ---

class ThemePreference(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class DateFormatPreference(str, Enum):
    DD_MM_YYYY = "DD/MM/YYYY"
    MM_DD_YYYY = "MM/DD/YYYY"
    YYYY_MM_DD = "YYYY-MM-DD"


class TimeFormatPreference(str, Enum):
    TWELVE = "12h"
    TWENTY_FOUR = "24h"


class DigestFrequency(str, Enum):
    REALTIME = "realtime"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    NONE = "none"


# --- User Settings ---

class UserSettingsBase(BaseModel):
    """Personal user preferences that follow the individual across devices."""

    # Appearance
    theme: ThemePreference = ThemePreference.LIGHT
    language: str = "en"
    timezone: str = "Africa/Lagos"
    date_format: DateFormatPreference = DateFormatPreference.DD_MM_YYYY
    time_format: TimeFormatPreference = TimeFormatPreference.TWELVE

    # Notifications
    email_notifications: bool = True
    push_notifications: bool = False
    notify_on_visitor_check_in: bool = True
    notify_on_appointment_reminder: bool = True
    notify_on_incident_created: bool = True
    notify_on_dsr_received: bool = True
    notify_on_system_alert: bool = True
    digest_frequency: DigestFrequency = DigestFrequency.REALTIME

    # Dashboard
    dashboard_action_order: List[str] = Field(default_factory=list)
    dashboard_collapsed_sections: List[str] = Field(default_factory=list)


class UserSettingsCreate(UserSettingsBase):
    """Internal creation schema — built by the service layer."""
    user_id: str
    user_type: str  # "admin" or "system_user"
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class UserSettingsUpdate(BaseModel):
    """Partial update — all fields optional."""

    # Appearance
    theme: Optional[ThemePreference] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    date_format: Optional[DateFormatPreference] = None
    time_format: Optional[TimeFormatPreference] = None

    # Notifications
    email_notifications: Optional[bool] = None
    push_notifications: Optional[bool] = None
    notify_on_visitor_check_in: Optional[bool] = None
    notify_on_appointment_reminder: Optional[bool] = None
    notify_on_incident_created: Optional[bool] = None
    notify_on_dsr_received: Optional[bool] = None
    notify_on_system_alert: Optional[bool] = None
    digest_frequency: Optional[DigestFrequency] = None

    # Dashboard
    dashboard_action_order: Optional[List[str]] = None
    dashboard_collapsed_sections: Optional[List[str]] = None

    last_updated: int = Field(default_factory=lambda: int(time.time()))


class UserSettingsOut(UserSettingsBase):
    """Response schema for user settings."""
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: Optional[str] = None
    user_type: Optional[str] = None
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


# --- User Preferences (key-value store) ---

class UserPreferenceUpdate(BaseModel):
    """Single preference key-value pair."""
    key: str = Field(..., max_length=100)
    value: Any = Field(...)

    @model_validator(mode="after")
    def validate_value_size(self):
        import json
        serialized = json.dumps(self.value)
        if len(serialized) > 16384:  # 16 KB limit per key
            raise ValueError("Preference value must be under 16 KB")
        return self


class UserPreferencesOut(BaseModel):
    """All preferences as a flat dict."""
    preferences: dict[str, Any] = Field(default_factory=dict)
