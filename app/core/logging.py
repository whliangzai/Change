"""JSON logging with conservative redaction for operational safety."""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = {"authorization", "password", "secret", "token", "database_url", "queue_url"}
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<key>(?:access_|refresh_)?token|authorization|password|secret|api[_-]?key|"
    r"queue[_-]?url|database[_-]?url)\s*(?P<separator>=|:)\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_AUTHORIZATION_BEARER_CREDENTIAL = re.compile(
    r"\bauthorization\s*:\s*bearer\s+[^\s,;]+",
    re.IGNORECASE,
)
_BARE_BEARER_CREDENTIAL = re.compile(
    r"\bbearer\s+(?!of\b)[A-Za-z0-9._~+/=-]+(?=$|[\s,;])",
    re.IGNORECASE,
)
_CREDENTIAL_URL = re.compile(r"(?:postgres(?:ql)?|redis)://[^\s,;]+", re.IGNORECASE)


def sanitize_text(value: str) -> str:
    """Mask key/value secrets and credential-bearing URLs in arbitrary log text."""
    sanitized = _AUTHORIZATION_BEARER_CREDENTIAL.sub("***", value)
    sanitized = _BARE_BEARER_CREDENTIAL.sub("***", sanitized)
    sanitized = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}***", sanitized
    )
    return _CREDENTIAL_URL.sub("***", sanitized)


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
    if isinstance(value, str):
        return sanitize_text(value)
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
        if record.exc_text:
            payload["exception"] = record.exc_text
        elif record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
