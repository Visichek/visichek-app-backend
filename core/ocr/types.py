from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OCRResult:
    full_name: str | None = None
    id_number: str | None = None
    id_type: str | None = None
    date_of_birth: str | None = None
    confidence: float = 0.0
    raw_data: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: str | None = None
