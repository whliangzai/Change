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
    assert "历史股票池" in page.text
    assert "历史交易日" in page.text
    assert "不是委托价" in page.text
    assert 'id="pool-result-count"' in page.text
    assert 'id="pool-reset"' in page.text
    assert 'class="primary" type="submit"' in page.text
    assert 'class="button-quiet" type="button">重置筛选</button>' in page.text
    assert '<th scope="col">状态来源</th>' not in page.text
    assert '<th scope="col">来源批次</th>' not in page.text
    assert '<th class="action-column" scope="col">操作</th>' in page.text
    assert 'aria-busy="true"' in page.text
    assert "renderFailure" in script.text
    assert "requestId(error)" in script.text
    assert "status === 403" in script.text
    assert 'button.textContent = "重试"' in script.text
    assert "formatNumber" in script.text
    assert "openBarsForSymbol" in script.text
    assert 'aria-expanded="false"' in script.text
    assert "scrollIntoView" in script.text
    assert "state-quiet" in script.text
    assert "pagination-range" in script.text
