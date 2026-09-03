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


def test_structured_logs_redact_bearer_tokens_in_messages_and_exception_text() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer bearer-token-should-not-leak",
        (),
        None,
    )
    record.exc_text = "bearer bearer-token-alone-should-not-leak"

    rendered = formatter.format(record)

    assert "bearer-token-should-not-leak" not in rendered
    assert "bearer-token-alone-should-not-leak" not in rendered
    assert "***" in rendered


def test_structured_logs_redact_opaque_alphanumeric_bearer_tokens() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "bearer opaqueTokenOnlyAlphaNum1234567890",
        (),
        None,
    )
    record.exc_text = "bearer exceptionTokenOnlyAlphaNum1234567890"

    rendered = formatter.format(record)

    assert "opaqueTokenOnlyAlphaNum1234567890" not in rendered
    assert "exceptionTokenOnlyAlphaNum1234567890" not in rendered
    assert "***" in rendered


def test_structured_logs_redact_short_opaque_bearer_tokens() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "bearer abc123",
        (),
        None,
    )
    record.exc_text = "bearer z9Y8x7"

    rendered = formatter.format(record)

    assert "abc123" not in rendered
    assert "z9Y8x7" not in rendered
    assert "***" in rendered


def test_structured_logs_preserve_bearer_as_an_ordinary_word() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "A bearer of ordinary instruments is not a credential.",
        (),
        None,
    )

    rendered = formatter.format(record)

    assert "bearer of ordinary instruments" in rendered


def test_structured_logs_redact_case_insensitive_sensitive_fields_and_multitoken_values() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "PASSWORD=canary first second authorization=Bearer canary:opaque?value",
        (),
        None,
    )
    record.exc_text = "Auth_Secret_Key=canary secret api_key=canary-key TOKEN=canary-token"

    rendered = formatter.format(record)

    for secret in ("canary", "opaque", "secret", "canary-key", "canary-token"):
        assert secret not in rendered
    assert "ordinary" not in rendered
