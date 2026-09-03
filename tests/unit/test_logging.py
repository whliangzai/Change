import logging

from app.core.logging import StructuredJsonFormatter


def test_structured_logs_redact_sensitive_message_and_exception_text() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "password=hunter2 refresh_token=abc secret=top api_key=private-key-value queue_url=redis://u:p@host",
        (),
        None,
    )
    record.exc_text = "database_url=postgresql://user:password@host/db"

    rendered = formatter.format(record)

    for secret in (
        "hunter2",
        "abc",
        "top",
        "private-key-value",
        "redis://",
        "postgresql://",
        "user:password",
    ):
        assert secret not in rendered
    assert "***" in rendered
