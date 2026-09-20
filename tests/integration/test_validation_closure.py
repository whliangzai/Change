from csv import DictReader
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.application.data_import_service import DataImportApplicationService
from app.core.config import load_settings
from app.core.errors import DataUnavailableError, StateConflictError
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories import research as research_repository
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.main import create_app


def _bar(symbol: str, trade_date: date, close: Decimal) -> dict[str, str]:
    return {
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "open": str(close),
        "high": str(close + Decimal("0.10")),
        "low": str(close - Decimal("0.10")),
        "close": str(close),
        "volume": "10000",
        "amount": "100000",
        "adjustment_factor": "1.0",
        "available_at": f"{trade_date.isoformat()}T18:00:00+00:00",
        "open_limit_up": "false",
        "open_limit_down": "false",
        "close_limit_up": "false",
        "close_limit_down": "false",
    }


def _seed_trade_calendar(engine, dates: list[date]) -> None:
    first = min(dates)
    warmup = [first - timedelta(days=offset) for offset in range(60, 0, -1)]
    with Session(engine) as session:
        session.add_all(
            models.TradeCalendar(exchange="SSE", trade_date=trade_date, is_open=True)
            for trade_date in (*warmup, *dates)
        )
        session.commit()


def _published_strategy(repository: SqlAlchemyResearchRepository, owner_id):
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "change_reason": "test", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    return strategy


def test_daily_flow_rejects_a_quality_available_batch_without_its_benchmark(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-benchmark.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": "synthetic.csv",
            "license_note": "authorized synthetic test data",
        },
        [
            _bar("600000.SH", date(2026, 9, day), Decimal("10") + Decimal(day) / 10)
            for day in range(1, 23)
        ],
    )
    strategy = _published_strategy(repository, owner_id)

    with pytest.raises(DataUnavailableError, match="benchmark market snapshot is unavailable"):
        repository.run_daily_flow(
            owner_id,
            {
                "data_batch_id": batch["batch_id"],
                "strategy_version_id": strategy["strategy_version_id"],
                "cost_config_id": "cost_v1",
                "rule_config_id": "rule_v1",
                "as_of_date": "2026-09-21",
                "information_cutoff_at": datetime(2026, 9, 21, 18, tzinfo=UTC).isoformat(),
                "idempotency_key": "missing-benchmark",
            },
        )


def test_backtest_api_rejects_a_quality_available_batch_without_its_benchmark(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'missing-backtest-benchmark.db'}"
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )
    client = TestClient(create_app(settings=settings))
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "missing-backtest-benchmark-login"},
        json={"username": "admin", "password": "admin"},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    principal = client.app.state.authenticator.authenticate_access_token(token)
    repository = client.app.state.repository
    batch = repository.import_daily_bars(
        principal.user_id,
        {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": "stock-only.csv",
            "license_note": "authorized synthetic test data",
        },
        [
            _bar("600000.SH", date(2026, 9, day), Decimal("10") + Decimal(day) / 10)
            for day in range(1, 23)
        ],
    )
    strategy = _published_strategy(repository, principal.user_id)

    response = client.post(
        "/api/v1/backtests",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "missing-backtest-benchmark",
        },
        json={
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "start_date": "2026-09-01",
            "train_end": "2026-09-10",
            "valid_end": "2026-09-15",
            "oos_start": "2026-09-16",
            "end_date": "2026-09-21",
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BT_DATA_UNAVAILABLE"
    assert repository.list_backtests(principal.user_id, None, 1, 50)["total"] == 0


def test_daily_flow_rejects_insufficient_benchmark_history_before_signal_generation(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'short-history.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": "synthetic.csv",
            "license_note": "authorized synthetic test data",
        },
        [
            _bar(
                symbol,
                date(2026, 9, day),
                Decimal("100") if symbol == "000300.SH" else Decimal("10"),
            )
            for day in range(1, 6)
            for symbol in ("600000.SH", "000300.SH")
        ],
    )
    strategy = _published_strategy(repository, owner_id)

    with pytest.raises(DataUnavailableError, match="at least 21 visible benchmark trading days"):
        repository.run_daily_flow(
            owner_id,
            {
                "data_batch_id": batch["batch_id"],
                "strategy_version_id": strategy["strategy_version_id"],
                "cost_config_id": "cost_v1",
                "rule_config_id": "rule_v1",
                "as_of_date": "2026-09-05",
                "information_cutoff_at": datetime(2026, 9, 5, 18, tzinfo=UTC).isoformat(),
                "idempotency_key": "short-benchmark-history",
            },
        )


def test_daily_flow_records_an_explanation_when_complete_data_has_no_candidates(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'no-candidates.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": "synthetic.csv",
            "license_note": "authorized synthetic test data",
        },
        [
            _bar(
                symbol,
                date(2026, 9, day),
                Decimal("100") if symbol == "000300.SH" else Decimal("10"),
            )
            for day in range(1, 23)
            for symbol in ("600000.SH", "000300.SH")
        ],
    )
    strategy = _published_strategy(repository, owner_id)

    run = repository.run_daily_flow(
        owner_id,
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "as_of_date": "2026-09-21",
            "information_cutoff_at": datetime(2026, 9, 21, 18, tzinfo=UTC).isoformat(),
            "idempotency_key": "no-candidates",
        },
    )
    report = repository.daily_report(date(2026, 9, 21), owner_id)

    assert run["status"] == "SUCCEEDED"
    assert run["plan_count"] == 0
    assert report["candidate_status"] == "NO_CANDIDATES"
    assert report["candidate_reason"] == "No securities passed the published strategy filters"


def test_authorized_simulated_fixture_imports_and_generates_a_t_plus_one_plan(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fixture-closure.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    source = Path(__file__).parents[1] / "fixtures" / "authorized_simulated_daily_bars.csv"
    batch = DataImportApplicationService(repository).import_file(
        owner_id,
        {
            "source_name": "authorized-simulated-fixture",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "Authorized synthetic test data; not market data.",
            "available_at": "2026-09-21T18:00:00+00:00",
            "information_cutoff_at": "2026-09-21T18:00:00+00:00",
        },
    )
    strategy = _published_strategy(repository, owner_id)

    run = repository.run_daily_flow(
        owner_id,
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "as_of_date": "2026-09-21",
            "information_cutoff_at": "2026-09-21T18:00:00+00:00",
            "idempotency_key": "fixture-daily-flow",
        },
    )

    assert batch["quality_status"] == "AVAILABLE"
    assert batch["record_count"] == 44
    assert run["status"] == "SUCCEEDED"
    assert run["signal_symbols"] == ["600000.SH"]
    assert run["plan_count"] == 1
    assert run["plan_execution_dates"] == ["2026-09-22"]


def test_admin_can_complete_authorized_http_validation_closure(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'http-validation-closure.db'}"
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )
    client = TestClient(create_app(settings=settings))
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "closure-login"},
        json={"username": "admin", "password": "admin"},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    source = Path(__file__).parents[1] / "fixtures" / "authorized_simulated_daily_bars.csv"
    with source.open(encoding="utf-8", newline="") as handle:
        fixture_dates = sorted(
            {date.fromisoformat(row["trade_date"]) for row in DictReader(handle)}
        )
    calendar_engine = create_engine(database_url)
    _seed_trade_calendar(calendar_engine, fixture_dates)
    calendar_engine.dispose()
    import_body = {
        "source_name": "authorized-http-fixture",
        "data_type": "DAILY_BAR",
        "file_location": str(source),
        "license_note": "Authorized synthetic test data; not market data.",
        "available_at": "2026-09-21T18:00:00+00:00",
        "information_cutoff_at": "2026-09-21T18:00:00+00:00",
    }

    assert client.get("/data-import").status_code == 200
    assert client.get("/daily-flow").status_code == 200
    imported = client.post(
        "/api/v1/data/imports",
        headers=headers | {"Idempotency-Key": "closure-import"},
        json=import_body,
    )
    imported_replay = client.post(
        "/api/v1/data/imports",
        headers=headers | {"Idempotency-Key": "closure-import"},
        json=import_body,
    )
    assert imported.status_code == imported_replay.status_code == 201
    batch_id = imported.json()["data"]["batch_id"]
    assert imported_replay.json()["data"]["batch_id"] == batch_id

    created = client.post(
        "/api/v1/strategies",
        headers=headers | {"Idempotency-Key": "closure-strategy"},
        json={
            "name": "HTTP fixture trend",
            "change_reason": "authorized local validation",
            "parameters": {"min_amount_ratio": "0"},
        },
    )
    assert created.status_code == 201
    strategy_id = created.json()["data"]["strategy_version_id"]
    published = client.post(
        f"/api/v1/strategies/{strategy_id}/submit-review",
        headers=headers | {"Idempotency-Key": "closure-publish"},
        json={"decision": "PUBLISH", "review_note": "admin validation release"},
    )
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "PUBLISHED"

    daily_body = {
        "data_batch_id": batch_id,
        "strategy_version_id": strategy_id,
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v1",
        "as_of_date": "2026-09-21",
        "information_cutoff_at": "2026-09-21T18:00:00+00:00",
    }
    daily = client.post(
        "/api/v1/daily-flows",
        headers=headers | {"Idempotency-Key": "closure-daily"},
        json=daily_body,
    )
    daily_replay = client.post(
        "/api/v1/daily-flows",
        headers=headers | {"Idempotency-Key": "closure-daily"},
        json=daily_body,
    )
    assert daily.status_code == daily_replay.status_code == 202
    assert daily.json()["data"]["run_id"] == daily_replay.json()["data"]["run_id"]
    assert daily.json()["data"]["plan_count"] == 1
    report = client.get("/api/v1/daily-reports/2026-09-21", headers=headers)
    plans = client.get("/api/v1/order-plans?execution_date=2026-09-22", headers=headers)
    assert report.status_code == plans.status_code == 200
    plan = plans.json()["data"]["items"][0]

    class BeforePlanExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 9, 22, 1, 0, tzinfo=UTC)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(research_repository, "datetime", BeforePlanExpiry)
    confirmed = client.post(
        f"/api/v1/order-plans/{plan['plan_id']}/confirm",
        headers=headers | {"Idempotency-Key": "closure-confirm"},
        json={
            "decision": "CONFIRM",
            "expected_version": plan["version"],
            "review_note": "reviewed",
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "CONFIRMED"

    execution_body = {
        "plan_id": plan["plan_id"],
        "execution_type": "MANUAL_ENTRY",
        "executed_at": "2026-09-22T01:00:00+00:00",
        "quantity": plan["quantity"],
        "price": plan["reference_low"],
        "commission": "5.00",
        "stamp_tax": "0.00",
        "transfer_fee": "0.01",
        "other_fee": "0.00",
        "unfilled_quantity": 0,
        "note": "manual simulated fill",
    }
    execution = client.post(
        "/api/v1/executions",
        headers=headers | {"Idempotency-Key": "closure-execution"},
        json=execution_body,
    )
    execution_replay = client.post(
        "/api/v1/executions",
        headers=headers | {"Idempotency-Key": "closure-execution"},
        json=execution_body,
    )
    assert execution.status_code == execution_replay.status_code == 201
    assert execution.json()["data"]["status"] == "FILLED"
    assert (
        execution.json()["data"]["execution_id"] == execution_replay.json()["data"]["execution_id"]
    )

    backtest_body = {
        "data_batch_id": batch_id,
        "strategy_version_id": strategy_id,
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v2",
        "start_date": "2026-09-18",
        "train_end": "2026-09-18",
        "valid_end": "2026-09-19",
        "oos_start": "2026-09-20",
        "end_date": "2026-09-21",
        "benchmark_symbol": "000300.SH",
        "initial_equity": "20000.00",
    }
    backtest = client.post(
        "/api/v1/backtests",
        headers=headers | {"Idempotency-Key": "closure-backtest"},
        json=backtest_body,
    )
    assert backtest.status_code == 202
    run_id = backtest.json()["data"]["run_id"]
    completed = client.post(
        f"/api/v1/backtests/{run_id}/execute",
        headers=headers | {"Idempotency-Key": "closure-backtest-execute"},
    )
    report_response = client.get(f"/api/v1/backtests/{run_id}/report", headers=headers)
    assert completed.status_code == 202
    assert report_response.status_code == 200
    completed_data = completed.json()["data"]
    assert completed_data["status"] == "SUCCEEDED", completed_data.get("result_summary")
    events, _ = client.app.state.audit_writer.page(1, 100)
    actions = {event["action"] for event in events}
    assert {
        "DATA_IMPORT",
        "STRATEGY_CREATE",
        "STRATEGY_REVIEW",
        "DAILY_FLOW_RUN",
        "ORDER_PLAN_CONFIRM",
        "EXECUTION_MANUAL_ENTRY",
        "BACKTEST_CREATE",
        "BACKTEST_EXECUTE",
    } <= actions


def test_authorized_simulated_fixture_has_weekday_history_and_a_later_execution_date() -> None:
    source = Path(__file__).parents[1] / "fixtures" / "authorized_simulated_daily_bars.csv"
    with source.open(encoding="utf-8", newline="") as handle:
        dates = sorted({date.fromisoformat(row["trade_date"]) for row in DictReader(handle)})

    assert len(dates) == 22
    assert all(value.weekday() < 5 for value in dates)
    assert len([value for value in dates if value <= dates[-2]]) >= 20
    assert dates[-1] > dates[-2]


def test_development_bootstrap_seeds_traceable_default_versions_once(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'seeded-defaults.db'}"
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )

    create_app(settings=settings)
    create_app(settings=settings)

    engine = create_engine(database_url)
    with Session(engine) as session:
        cost = session.scalar(
            select(models.CostConfigVersion).where(models.CostConfigVersion.version == "cost_v1")
        )
        rule = session.scalar(
            select(models.RuleConfigVersion).where(models.RuleConfigVersion.version == "rule_v1")
        )
        seed_events = session.scalar(
            select(func.count())
            .select_from(models.AuditEvent)
            .where(models.AuditEvent.action == "LOCAL_DEFAULT_CONFIG_INITIALIZE")
        )

    assert cost is not None
    assert rule is not None
    assert cost.config["seed_origin"] == "local-development"
    assert rule.config["seed_origin"] == "local-development"
    assert rule.source_urls
    assert seed_events == 1


def test_default_versions_require_explicit_initialization_outside_app_bootstrap(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'explicit-defaults.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    owner_id = uuid4()
    source = Path(__file__).parents[1] / "fixtures" / "authorized_simulated_daily_bars.csv"
    batch = DataImportApplicationService(repository).import_file(
        owner_id,
        {
            "source_name": "authorized-simulated-explicit-defaults",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "Authorized synthetic test data; not market data.",
        },
    )
    strategy = _published_strategy(repository, owner_id)
    payload = {
        "data_batch_id": batch["batch_id"],
        "strategy_version_id": strategy["strategy_version_id"],
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v1",
        "start_date": "2026-09-01",
        "end_date": "2026-09-21",
        "train_end": "2026-09-10",
        "valid_end": "2026-09-15",
        "oos_start": "2026-09-16",
        "benchmark_symbol": "000300.SH",
        "owner_id": str(owner_id),
    }

    assert repository.dependencies_available(payload) is False
    initialized = repository.initialize_local_default_versions()

    assert initialized["created"] is True
    assert repository.dependencies_available(payload) is True


def test_expired_t_plus_one_plan_cannot_be_confirmed(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'expired-plan.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": "synthetic.csv",
            "license_note": "authorized synthetic test data",
        },
        [
            _bar(
                symbol,
                date(2024, 9, day),
                Decimal("100") + Decimal(day)
                if symbol == "000300.SH"
                else Decimal("10") + Decimal(day) / 10,
            )
            for day in range(1, 23)
            for symbol in ("600000.SH", "000300.SH")
        ],
    )
    strategy = _published_strategy(repository, owner_id)
    run = repository.run_daily_flow(
        owner_id,
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "as_of_date": "2024-09-21",
            "information_cutoff_at": "2024-09-21T18:00:00+00:00",
            "idempotency_key": "expired-plan-run",
        },
    )
    plan = repository.list_plans(date(2024, 9, 22), 1, 50, owner_id)["items"][0]

    assert run["plan_count"] == 1
    with pytest.raises(StateConflictError, match="plan has expired"):
        repository.confirm_plan(plan["plan_id"], owner_id, "CONFIRM", 1, "reviewed")
