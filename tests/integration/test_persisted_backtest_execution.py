from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.infrastructure.db import models
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.main import create_app


def _bar(trade_date: date) -> dict[str, object]:
    return {
        "symbol": "600000.SH",
        "trade_date": trade_date.isoformat(),
        "open": "10.00",
        "high": "10.20",
        "low": "9.90",
        "close": "10.10",
        "volume": "10000",
        "amount": "30000000.00",
        "adjustment_factor": "1.0",
        "available_at": f"{trade_date.isoformat()}T18:00:00+00:00",
        "limit_up": False,
        "limit_down": False,
    }


def _benchmark_bar(trade_date: date) -> dict[str, object]:
    return {
        "symbol": "000300.SH",
        "trade_date": trade_date.isoformat(),
        "open": "100.00",
        "high": "100.20",
        "low": "99.90",
        "close": "100.10",
        "volume": "10000",
        "amount": "101000.00",
        "adjustment_factor": "1.0",
        "available_at": f"{trade_date.isoformat()}T18:00:00+00:00",
        "limit_up": False,
        "limit_down": False,
    }


def _trend_bar(
    symbol: str, trade_date: date, close: Decimal, amount: str = "30000000"
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "open": str(close - Decimal("0.05")),
        "high": str(close + Decimal("0.10")),
        "low": str(close - Decimal("0.10")),
        "close": str(close),
        "volume": "10000",
        "amount": amount,
        "adjustment_factor": "1.0",
        "available_at": f"{trade_date.isoformat()}T18:00:00+00:00",
        "limit_up": False,
        "limit_down": False,
    }


def _history_dates(end: date, count: int = 90) -> tuple[date, ...]:
    return tuple(end - timedelta(days=offset) for offset in range(count - 1, -1, -1))


def _seed_calendar(database_url: str, dates: tuple[date, ...]) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        session.add_all(
            models.TradeCalendar(exchange="SSE", trade_date=trade_date, is_open=True)
            for trade_date in dates
        )
        session.commit()
    engine.dispose()


def test_development_api_executes_persisted_backtest_with_real_runner(tmp_path) -> None:
    owner_id = uuid4()
    account = LocalAccount(
        owner_id,
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER, Role.REVIEWER}),
    )
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'execution.db'}",
        }
    )
    app = create_app(settings=settings, accounts={"user": account})
    dates = _history_dates(date(2026, 9, 5))
    _seed_calendar(settings.database_url, dates)
    batch = app.state.repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/bars.csv",
            "license_note": "licensed",
        },
        [item for trade_date in dates for item in (_bar(trade_date), _benchmark_bar(trade_date))],
    )
    strategy = app.state.repository.create_strategy(
        owner_id, {"name": "trend", "parameters": {}, "change_reason": "initial"}
    )
    app.state.repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "execute-login"},
        json={"username": "user", "password": "pw"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    created = client.post(
        "/api/v1/backtests",
        headers=headers | {"Idempotency-Key": "execute-create"},
        json={
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v2",
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
            "train_end": "2026-09-02",
            "valid_end": "2026-09-03",
            "oos_start": "2026-09-04",
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
            "mode": "BACKTEST",
        },
    )
    run_id = created.json()["data"]["run_id"]
    listed = client.get("/api/v1/backtests?page=1&page_size=50", headers=headers)

    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["run_id"] == run_id

    executed = client.post(
        f"/api/v1/backtests/{run_id}/execute",
        headers=headers | {"Idempotency-Key": "execute-run"},
    )

    assert executed.status_code == 202
    assert executed.json()["data"]["status"] == "SUCCEEDED"
    loaded = client.get(f"/api/v1/backtests/{run_id}", headers=headers)
    report = client.get(f"/api/v1/backtests/{run_id}/report", headers=headers)
    assert loaded.json()["data"]["result_usable"] is True
    assert report.json()["data"]["status"] == "SUCCEEDED"
    assert report.json()["data"]["export"]["report_type"] == "BACKTEST"
    assert report.json()["data"]["export"]["content_hash"] == loaded.json()["data"]["snapshot_hash"]
    assert Decimal(report.json()["data"]["metrics"]["total_return"]) == Decimal("0")
    restarted = SqlAlchemyResearchRepository.from_url(settings.database_url)
    restarted_report = restarted.get_run_child(run_id, owner_id, "report")
    assert restarted_report is not None
    assert restarted_report["export"]["file_path"] == f"reports/{run_id}.json"


def test_persisted_backtest_generates_causal_strategy_trades(tmp_path) -> None:
    owner_id = uuid4()
    account = LocalAccount(
        owner_id,
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER, Role.REVIEWER}),
    )
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'trend-execution.db'}",
        }
    )
    app = create_app(settings=settings, accounts={"user": account})
    dates = _history_dates(date(2026, 9, 26))
    _seed_calendar(settings.database_url, dates)
    rows: list[dict[str, object]] = []
    for index, trade_date in enumerate(dates, start=1):
        rows.append(_trend_bar("600000.SH", trade_date, Decimal("10") + Decimal(index) / 10))
        rows.append(_trend_bar("000300.SH", trade_date, Decimal("100") + Decimal(index)))
    batch = app.state.repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/trend-bars.csv",
            "license_note": "licensed",
        },
        rows,
    )
    strategy = app.state.repository.create_strategy(
        owner_id,
        {
            "name": "trend",
            "parameters": {"min_amount_ratio": "0"},
            "change_reason": "initial",
        },
    )
    app.state.repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "trend-login"},
        json={"username": "user", "password": "pw"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    created = client.post(
        "/api/v1/backtests",
        headers=headers | {"Idempotency-Key": "trend-create"},
        json={
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v2",
            "start_date": "2026-09-01",
            "end_date": "2026-09-26",
            "train_end": "2026-09-10",
            "valid_end": "2026-09-15",
            "oos_start": "2026-09-16",
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
            "mode": "BACKTEST",
        },
    )
    run_id = created.json()["data"]["run_id"]
    executed = client.post(
        f"/api/v1/backtests/{run_id}/execute",
        headers=headers | {"Idempotency-Key": "trend-run"},
    )

    assert executed.status_code == 202
    assert executed.json()["data"]["status"] == "SUCCEEDED"
    trades = client.get(f"/api/v1/backtests/{run_id}/trades", headers=headers)
    assert trades.status_code == 200
    assert trades.json()["data"]["total"] >= 1
    assert trades.json()["data"]["items"][0]["symbol"] == "600000.SH"
