"""Rendered contracts for run details, manual records, and management audit views."""

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(
        create_app(settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}))
    )


def test_backtest_detail_exposes_run_evidence_and_recoverable_result_states() -> None:
    client = _client()

    page = client.get("/backtests/run-123/view")
    script = client.get("/static/js/backtest_detail.js")

    assert page.status_code == 200
    for identifier in (
        'id="backtest-preconditions"',
        'id="run-unavailable" class="state unavailable"',
        'id="report-unavailable" class="state unavailable"',
        'id="trades-unavailable" class="state unavailable"',
    ):
        assert identifier in page.text
    assert 'class="table-wrap"' in page.text
    assert "data_batch_id" in script.text
    assert "renderFailure" in script.text
    assert "request_id" in script.text


def test_manual_execution_page_keeps_partial_fill_and_server_traceability_visible() -> None:
    client = _client()

    page = client.get("/executions")
    script = client.get("/static/js/executions.js")

    assert page.status_code == 200
    for identifier in (
        'id="execution-plan-context"',
        'id="execution-form-error"',
        'id="execution-plan-error"',
        'aria-describedby="execution-plan-error"',
        "data-submit-label=",
    ):
        assert identifier in page.text
    assert "PARTIALLY_FILLED" in script.text
    assert "Idempotency-Key" in script.text
    assert "payload.request_id" in script.text
    assert "setFieldError" in script.text
    assert "isSubmitting" in script.text


def test_admin_page_requires_retry_evidence_and_preserves_audit_identifiers() -> None:
    client = _client()

    page = client.get("/admin")
    script = client.get("/static/js/admin.js")

    assert page.status_code == 200
    for identifier in (
        'id="admin-permission"',
        'id="retry-confirmation"',
        'id="retry-note"',
        'id="retry-form-error"',
        'id="jobs-result-count"',
        'id="audit-result-count"',
    ):
        assert identifier in page.text
    assert "renderFailure" in script.text
    assert "request_id" in script.text
    assert "Idempotency-Key" in script.text
