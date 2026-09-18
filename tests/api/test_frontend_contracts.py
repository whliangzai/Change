import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
APP_CSS = ROOT / "static" / "css" / "app.css"
RESEARCH_CSS = ROOT / "static" / "css" / "research-pages.css"
BASE_TEMPLATE = ROOT / "templates" / "base.html"
DATA_QUALITY_JS = ROOT / "static" / "js" / "data_quality.js"


def test_base_shell_uses_keyboard_safe_collapsible_navigation() -> None:
    html = BASE_TEMPLATE.read_text(encoding="utf-8")

    assert 'id="nav-toggle"' in html
    assert 'aria-controls="main-nav"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="main-nav"' in html
    assert 'data-open="false"' in html
    assert 'nav.querySelector("a")?.focus()' in html
    assert '/static/css/app.css?v=layout-fix-20260918-3' in html
    assert "prototype-pages.css" not in html
    assert not (ROOT / "static" / "css" / "prototype-pages.css").exists()


def test_shared_css_exposes_the_workbench_tokens_and_mobile_overflow_boundary() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "--bg",
        "--surface",
        "--ink",
        "--muted",
        "--line",
        "--accent",
        "--ok",
        "--warning",
        "--danger",
        "--panel-radius",
        "--control-radius",
        "--content-width",
        "--wide-content-width",
    ):
        assert token in css
    assert "--content-width: 1200px" in css
    assert "--wide-content-width: 1440px" in css
    assert "--control-height: 40px" in css
    assert "@media (max-width: 899px)" in css
    assert 'nav:not([data-open="true"])' in css
    assert (
        "main, body.wide-research-shell main, body.universe-shell main { padding: 20px 16px 44px; }"
        in css
    )
    assert "overflow-x: hidden" in css
    assert ".table-wrap { max-width: 100%; overflow: auto;" in css


def test_page_level_css_has_no_direct_hex_colors() -> None:
    for path in (APP_CSS, RESEARCH_CSS):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"(?<![-\w])#[0-9a-fA-F]{3,8}\b", line):
                assert line.lstrip().startswith("--"), f"direct color in {path}: {line}"


def test_shared_layout_constrains_icons_pagination_and_single_field_actions() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    quality_js = DATA_QUALITY_JS.read_text(encoding="utf-8")

    assert "button > svg, .button-link > svg, .api-reference > svg" in css
    assert "flex: 0 0 1rem" in css
    assert ".pagination { flex-wrap: wrap; min-width: 0; background: transparent;" in css
    assert ".pagination-button, .pagination-current" in css
    assert ":is(#execution-plan-filter, #plan-filter)" in css
    assert "grid-template-columns: minmax(240px, 420px) auto" in css
    assert "align-items: end" in css
    assert 'controls.className = "pagination-controls"' in quality_js
    assert 'summary.className = "pagination-range"' in quality_js


def test_existing_dom_contracts_remain_present_on_research_pages() -> None:
    required = {
        "data_quality.html": ('data-page="data-quality"', "batch-table", "quality-detail"),
        "universe.html": ('data-page="universe"', "pool-table", "bars-table"),
        "strategy_versions.html": (
            'data-page="strategies"',
            "strategy-id",
            "strategy-review-submit",
        ),
        "backtest_detail.html": ('data-page="backtest-detail"', "backtest-list", "run-summary"),
        "reports.html": ('data-page="reports"', "report-run-select", "report-content"),
    }
    for name, markers in required.items():
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in html, f"{name} lost DOM contract {marker}"


def test_order_plans_page_binds_query_date_and_rejects_invalid_dates() -> None:
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.core.config import load_settings
    from app.core.security import LocalAccount, PasswordHasher, Role
    from app.main import create_app

    account = LocalAccount(uuid4(), "user", PasswordHasher().hash("pw"), frozenset({Role.USER}))
    client = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"user": account},
        )
    )
    page = client.get("/order-plans?execution_date=2026-09-04")
    assert page.status_code == 200
    assert 'id="plan-execution-date"' in page.text
    assert 'value="2026-09-04"' in page.text
    assert client.get("/order-plans?execution_date=not-a-date").status_code == 400


def test_shared_state_request_evidence_tabs_and_report_summary_contracts() -> None:
    base = BASE_TEMPLATE.read_text(encoding="utf-8")
    import_page = (ROOT / "templates" / "data_import.html").read_text(encoding="utf-8")
    report_page = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

    assert "requestId" in base
    assert "statusLabel" in base
    assert "statusClass" in base
    assert 'role="tablist"' in import_page
    assert 'role="tab"' in import_page
    assert 'aria-controls="provider-import-panel"' in import_page
    assert 'aria-controls="local-import-panel"' in import_page
    assert 'id="report-metrics-table"' in report_page
    assert 'id="report-cost-table"' in report_page
    assert 'id="report-deviation-table"' in report_page
    assert '<details class="developer-evidence">' in report_page
