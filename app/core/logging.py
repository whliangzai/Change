"""JSON logging with conservative redaction for operational safety."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = {"authorization", "password", "secret", "token", "database_url", "queue_url"}


def redact(value: Any, key: str = "") -> Any:
    """Mask values whose key can expose credentials or bearer material."""
    if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
        return "***"
    if isinstance(value, dict):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "request_id": getattr(record, "request_id", None),
            "run_id": getattr(record, "run_id", None),
            "job_id": getattr(record, "job_id", None),
            "user_id": getattr(record, "user_id", None),
            "event_code": getattr(record, "event_code", None),
            "message": record.getMessage(),
        }
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
