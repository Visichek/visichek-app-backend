from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for production environments."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Propagate extra context fields if set
        for attr in ("request_id", "tenant_id", "user_id", "endpoint"):
            value = getattr(record, attr, None)
            if value is not None:
                log_obj[attr] = value
        return json.dumps(log_obj, default=str)


class HumanFormatter(logging.Formatter):
    """Readable log formatter for development environments."""

    FMT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT)


def configure_logging(log_level: str = "INFO", is_production: bool = False) -> None:
    """Configure root logger with appropriate formatter.

    Call this once during application startup, before any other initialisation.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    if is_production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Replace existing handlers to avoid duplicate output
    root.handlers = [handler]

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Convenience wrapper used across the project."""
    return logging.getLogger(name)
