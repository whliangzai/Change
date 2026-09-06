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


def access_client() -> TestClient:
    hasher = PasswordHasher()
    accounts = {
        "user": LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER})),
        "admin": LocalAccount(
            uuid4(), "admin", hasher.hash("pw"), frozenset({Role.USER, Role.ADMIN})
        ),
    }
    return TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts=accounts,
        )
    )


def login_page_access(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": f"page-login-{username}"},
        json={"username": username, "password": "pw"},
    )
    assert response.status_code == 200
    assert "research_page_access" in response.headers["set-cookie"]


def test_required_research_pages_render_with_compliance_notice() -> None:
    pages = (
        "/",
        "/login",
        "/dashboard",
        "/data-quality",
        "/universe",
        "/strategies",
        "/backtests",
        "/backtests/create",
        f"/backtests/{uuid4()}/view",
        "/daily-reports/2026-09-03",
        "/order-plans",
        "/executions",
        "/reports",
        "/admin",
    )

    test_client = client()
    for path in pages:
        response = test_client.get(path)
        assert response.status_code == 200, path
        assert "不构成投资建议" in response.text, path
        assert "不承诺收益" in response.text, path
        assert "不自动下单" in response.text, path


def test_research_pages_bind_to_real_api_endpoints() -> None:
    test_client = client()

    quality = test_client.get("/data-quality?batch_id=batch-123")
    universe = test_client.get("/universe?trade_date=2026-09-03")
    detail = test_client.get("/backtests/run-123/view")

    assert 'data-api-url="/api/v1/data/batches/batch-123/quality"' in quality.text
    assert 'data-api-url="/api/v1/securities/pool?trade_date=2026-09-03"' in universe.text
    assert 'data-api-url="/api/v1/backtests/run-123"' in detail.text
    assert "未加载" not in quality.text
    assert "未加载" not in detail.text


def test_research_pages_ship_interactive_api_clients_and_assets() -> None:
    test_client = client()

    login = test_client.get("/login")
    dashboard = test_client.get("/dashboard")
    backtest_create = test_client.get("/backtests/create")
    plans = test_client.get("/order-plans")
    static_client = test_client.get("/static/js/data_quality.js")
    backtest_client = test_client.get("/static/js/backtest_create.js")

    assert "/api/v1/auth/login" in login.text
    assert "Idempotency-Key" in login.text
    assert "/static/js/dashboard.js" in dashboard.text
    assert "/static/js/backtest_create.js" in backtest_create.text
    assert "/static/js/order_plans.js" in plans.text
    assert static_client.status_code == 200
    assert "data/batches" in static_client.text
    assert backtest_client.status_code == 200
    assert "/api/v1/backtests" in backtest_client.text


def test_base_shell_provides_landmarks_focus_and_responsive_navigation() -> None:
    response = client().get("/dashboard")

    assert 'class="skip-link"' in response.text
    assert 'id="main-content"' in response.text
    assert 'aria-label="主导航"' in response.text
    assert "button:focus-visible" in response.text
    assert "@media(max-width:700px)" in response.text
    assert "data-admin-nav hidden" in response.text


def test_login_page_exposes_field_errors_and_blocks_duplicate_submission() -> None:
    response = client().get("/login")

    assert 'id="login-username"' in response.text
    assert 'id="login-password"' in response.text
    assert 'id="username-error"' in response.text
    assert 'id="password-error"' in response.text
    assert "isSubmitting" in response.text
    assert 'aria-live="polite"' in response.text
    assert 'autocomplete="username"' in response.text
    assert 'autocomplete="current-password"' in response.text


def test_data_pages_use_the_shared_authenticated_api_client() -> None:
    test_client = client()

    for asset in ("/static/js/data_quality.js", "/static/js/universe.js"):
        response = test_client.get(asset)

        assert response.status_code == 200
        assert "window.ResearchApp.apiFetch" in response.text
        assert "localStorage.getItem('access_token')" not in response.text
        assert "localStorage.getItem('accessToken')" not in response.text


def test_admin_operation_pages_render_navigation_and_shared_api_clients() -> None:
    test_client = access_client()
    login_page_access(test_client, "admin")
    imported = test_client.get("/data-import")
    daily_flow = test_client.get("/daily-flow")
    navigation = test_client.get("/dashboard")

    assert "/data-import" in navigation.text
    assert "/daily-flow" in navigation.text
    assert "/static/js/data_import.js" in imported.text
    assert "/api/v1/data/imports" in imported.text
    assert "/static/js/daily_flow.js" in daily_flow.text
    assert "/api/v1/daily-flows" in daily_flow.text
    for asset in ("/static/js/data_import.js", "/static/js/daily_flow.js"):
        response = test_client.get(asset)
        assert response.status_code == 200
        assert "window.ResearchApp.apiFetch" in response.text
        assert "Idempotency-Key" not in response.text


def test_admin_operation_scripts_cover_quality_and_daily_result_states() -> None:
    test_client = access_client()
    login_page_access(test_client, "admin")
    imported = test_client.get("/static/js/data_import.js")
    daily_flow = test_client.get("/static/js/daily_flow.js")
    import_page = test_client.get("/data-import")
    daily_page = test_client.get("/daily-flow")

    for field in (
        "file_location",
        "source_name",
        "license_note",
        "available_at",
        "information_cutoff_at",
    ):
        assert field in import_page.text
    assert "quality_status" in imported.text
    assert "batch_id" in imported.text
    assert "质量错误与警告" in import_page.text
    for field in (
        "data_batch_id",
        "strategy_version_id",
        "cost_config_id",
        "rule_config_id",
        "as_of_date",
        "information_cutoff_at",
    ):
        assert field in daily_page.text
    assert 'name="strategy_version_id" id="daily-flow-strategy"' in daily_page.text
    assert "/daily-reports/" in daily_flow.text
    assert "/order-plans?execution_date=" in daily_flow.text
    assert "/api/v1/data/batches?page=1&page_size=200" in daily_flow.text
    assert "WARNING_AVAILABLE" in daily_flow.text
    assert "/api/v1/strategies?status=PUBLISHED&page=1&page_size=200" in daily_flow.text
    assert "PUBLISHED" in daily_flow.text
    assert "暂无可选择的已发布策略" in daily_flow.text
    assert "未生成候选或 T+1 计划" in daily_page.text


def test_data_import_client_omits_an_empty_optional_version() -> None:
    test_client = access_client()

    imported = test_client.get("/static/js/data_import.js")

    assert "const version = String(values.version || '').trim();" in imported.text
    assert "else delete values.version;" in imported.text


def test_admin_operation_clients_await_http_responses_before_parsing_json() -> None:
    test_client = access_client()

    for asset in ("/static/js/data_import.js", "/static/js/daily_flow.js"):
        response = test_client.get(asset)

        assert "readJson(await window.ResearchApp.apiFetch(url, options))" in response.text


def test_admin_pages_require_a_page_access_cookie_and_hide_management_navigation() -> None:
    test_client = access_client()

    for path in ("/data-import", "/daily-flow"):
        assert test_client.get(path).status_code == 401

    login_page_access(test_client, "user")
    for path in ("/data-import", "/daily-flow"):
        assert test_client.get(path).status_code == 403

    login_page_access(test_client, "admin")
    for path in ("/data-import", "/daily-flow"):
        assert test_client.get(path).status_code == 200
    navigation = test_client.get("/dashboard")
    assert "data-admin-nav hidden" in navigation.text
    assert "data-admin-nav" in navigation.text
