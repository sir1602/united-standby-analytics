"""Structured logging configuration for the United Standby Analytics pipeline."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON or structured key-value lines."""

    def __init__(self, json_output: bool = False) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)

        if self.json_output:
            return json.dumps(payload, default=str)

        extras_str = ""
        if isinstance(extra_fields, dict) and extra_fields:
            extras_str = " " + " ".join(f"{k}={v!r}" for k, v in extra_fields.items())
        return f"[{ts}] {record.levelname:<7} {record.name}: {record.getMessage()}{extras_str}"


def configure_logging(level: str = "INFO", json_output: bool = False) -> logging.Logger:
    """Configure root/package structured logger."""
    logger = logging.getLogger("standby")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(json_output=json_output))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
