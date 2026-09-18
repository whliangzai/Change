"""Rendered contracts for run details, manual records, and management audit views."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
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


def test_supplier_import_page_exposes_persistent_job_states_and_recovery_paths() -> None:
    hasher = PasswordHasher()
    client = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={
                "admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN}))
            },
        )
    )
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "supplier-page-login"},
        json={"username": "admin", "password": "pw"},
    ).json()["data"]["access_token"]
    client.cookies.set("research_page_access", token)

    page = client.get("/data-import")
    script = client.get("/static/js/data_import.js")

    assert page.status_code == 200
    for identifier in (
        'id="provider-import-form"',
        'id="provider-import-provider"',
        'id="provider-import-date"',
        'id="provider-import-scope"',
        'id="provider-import-state"',
        'id="provider-import-refresh"',
        'id="provider-import-links"',
    ):
        assert identifier in page.text
    assert "/api/v1/admin/data-imports/capabilities" in script.text
    assert "/api/v1/jobs/" in script.text
    assert "setInterval" in script.text
    assert "60000" in script.text
    assert "function resumeProviderPolling" in script.text
    assert "providerRefresh.addEventListener('click', resumeProviderPolling)" in script.text
    assert "getFullYear()" in script.text
    assert "providerDate.value = today.toISOString().slice(0, 10)" not in script.text
    assert "AKShare 只做校验" in page.text
