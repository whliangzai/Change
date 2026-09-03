"""Secrets must not cross the structured logging boundary."""

import json
import logging

from app.core.logging import StructuredJsonFormatter, redact


def test_structured_logging_redacts_nested_credentials_and_preserves_correlation_ids() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "quality",
        logging.INFO,
        __file__,
        1,
        "daily flow finished password=hunter2 bearer opaque-token-123",
        (),
        None,
    )
    record.request_id = "req-123"
    record.run_id = "run-123"
    record.exc_text = "queue_url=redis://user:secret@localhost/0"

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert payload["request_id"] == "req-123"
    assert payload["run_id"] == "run-123"
    for secret in ("hunter2", "opaque-token-123", "redis://", "user:secret"):
        assert secret not in rendered


def test_redact_masks_secret_key_variants_without_mutating_safe_values() -> None:
    payload = redact(
        {
            "access_token": "access-value",
            "Auth_Secret_Key": "secret-value",
            "database_url": "postgresql://user:pass@host/db",
            "run_id": "run-123",
            "count": 2,
        }
    )

    assert payload == {
        "access_token": "***",
        "Auth_Secret_Key": "***",
        "database_url": "***",
        "run_id": "run-123",
        "count": 2,
    }
