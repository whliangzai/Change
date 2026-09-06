from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.main import create_app


def client() -> TestClient:
    return TestClient(
        create_app(settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}))
    )


def test_data_quality_page_exposes_filter_context_and_recoverable_states() -> None:
    page = client().get("/data-quality")
    script = client().get("/static/js/data_quality.js")

    assert page.status_code == 200
    assert "质量闸门" in page.text
    assert 'id="batch-result-count"' in page.text
    assert 'id="batch-reset"' in page.text
    assert 'aria-busy="true"' in page.text
    assert "renderFailure" in script.text
    assert "requestId(error)" in script.text
    assert "status === 403" in script.text
    assert 'button.textContent = "重试"' in script.text


def test_universe_page_exposes_historical_data_boundaries_and_recoverable_states() -> None:
    page = client().get("/universe?trade_date=2026-09-03")
    script = client().get("/static/js/universe.js")

    assert page.status_code == 200
    assert "历史交易日" in page.text
    assert "不是委托价" in page.text
    assert 'id="pool-result-count"' in page.text
    assert 'id="pool-reset"' in page.text
    assert 'aria-busy="true"' in page.text
    assert "renderFailure" in script.text
    assert "requestId(error)" in script.text
    assert "status === 403" in script.text
    assert 'button.textContent = "重试"' in script.text
    assert "formatNumber" in script.text
