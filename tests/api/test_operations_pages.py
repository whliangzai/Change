from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def client() -> TestClient:
    hasher = PasswordHasher()
    account = LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER}))
    return TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"user": account},
        )
    )


def test_operations_pages_bind_independent_real_api_clients() -> None:
    test_client = client()
    expectations = {
        "/daily-reports/2026-09-03": ("/static/js/daily_report.js", "/api/v1/daily-reports/"),
        "/order-plans": ("/static/js/order_plans.js", "/api/v1/order-plans"),
        "/executions": ("/static/js/executions.js", "/api/v1/executions"),
        "/reports": ("/static/js/reports.js", "/api/v1/backtests"),
        "/admin": ("/static/js/admin.js", "/api/v1/jobs"),
    }

    for path, (script, api_path) in expectations.items():
        response = test_client.get(path)
        assert response.status_code == 200
        assert script in response.text
        assert api_path in response.text
        assert "不自动下单" in response.text


def test_overview_and_report_pages_expose_recoverable_state_regions() -> None:
    test_client = client()
    dashboard = test_client.get("/dashboard")
    daily_report = test_client.get("/daily-reports/2026-09-03")
    reports = test_client.get("/reports")
    dashboard_script = test_client.get("/static/js/dashboard.js")

    assert 'id="dashboard-data-availability"' in dashboard.text
    assert "数据来源" in dashboard_script.text
    assert "版本" in dashboard_script.text
    assert 'id="daily-report-unavailable" class="state unavailable"' in daily_report.text
    assert daily_report.text.count('class="table-wrap"') >= 3
    assert 'id="report-selection-state"' in reports.text
    assert 'id="report-unavailable" class="state unavailable"' in reports.text
