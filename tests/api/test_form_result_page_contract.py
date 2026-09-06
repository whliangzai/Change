"""Rendered contracts for form-and-result research workbench pages."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def _client() -> TestClient:
    account = LocalAccount(
        uuid4(),
        "admin",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER, Role.ADMIN}),
    )
    client = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"admin": account},
        )
    )
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "form-page-login"},
        json={"username": "admin", "password": "pw"},
    )
    assert login.status_code == 200
    return client


def test_form_result_pages_expose_persistent_states_and_nearby_errors() -> None:
    client = _client()

    imported = client.get("/data-import")
    created = client.get("/backtests/create")
    daily = client.get("/daily-flow")

    for response in (imported, created, daily):
        assert response.status_code == 200
        assert 'role="status"' in response.text
        assert 'class="field-error"' in response.text
        assert "aria-describedby=" in response.text
        assert "data-submit-label=" in response.text

    assert "质量闸门" in imported.text
    assert "模拟价不是委托价" in created.text
    assert "T+1 人工计划" in daily.text
    assert "不会自动下单" in daily.text


def test_form_result_clients_map_server_errors_and_keep_request_traceability() -> None:
    client = _client()

    for asset in (
        "/static/js/data_import.js",
        "/static/js/backtest_create.js",
        "/static/js/daily_flow.js",
    ):
        source = client.get(asset)
        assert source.status_code == 200
        assert "setFieldError" in source.text
        assert "clearFieldErrors" in source.text
        assert "error.payload?.request_id" in source.text
        assert "window.ResearchApp.renderState" in source.text
        assert "isSubmitting" in source.text
