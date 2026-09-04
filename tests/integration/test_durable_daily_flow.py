from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select

from app.core.errors import StateConflictError
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository


def _bar(symbol: str, day: int, close: Decimal) -> dict[str, str]:
    trade_date = date(2026, 9, day)
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
    }


def test_daily_flow_persists_signals_plans_report_and_export_idempotently(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-flow.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    rows = [
        bar
        for day in range(1, 27)
        for bar in (
            _bar("600000.SH", day, Decimal("10") + Decimal(day) / 10),
            _bar("000300.SH", day, Decimal("100") + Decimal(day)),
        )
    ]
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/daily-bars.csv",
            "license_note": "licensed",
        },
        rows,
    )
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    payload = {
        "data_batch_id": batch["batch_id"],
        "strategy_version_id": strategy["strategy_version_id"],
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v1",
        "as_of_date": "2026-09-25",
        "information_cutoff_at": "2026-09-25T18:00:00+00:00",
        "initial_equity": "20000.00",
        "max_investment_ratio": "0.70",
        "idempotency_key": "daily-2026-09-25",
    }

    first = repository.run_daily_flow(owner_id, payload)
    second = SqlAlchemyResearchRepository.from_engine(engine).run_daily_flow(owner_id, payload)

    assert first["status"] == "SUCCEEDED"
    assert first["run_id"] == second["run_id"]
    assert first["signal_symbols"] == ["600000.SH"]
    assert first["plan_count"] == 1
    assert first["plan_execution_dates"] == ["2026-09-26"]
    assert repository.daily_report(date(2026, 9, 25))["status"] == "SUCCEEDED"
    export = repository.create_export(first["run_id"], owner_id)
    assert export["status"] == "READY"

    with engine.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(models.SignalSnapshot)).scalar()
            == 1
        )
        assert connection.execute(select(func.count()).select_from(models.OrderPlan)).scalar() == 1
        assert (
            connection.execute(select(func.count()).select_from(models.PortfolioSnapshot)).scalar()
            == 1
        )
        assert (
            connection.execute(select(func.count()).select_from(models.DailyReport)).scalar() == 1
        )
        assert (
            connection.execute(select(func.count()).select_from(models.ReportArtifact)).scalar()
            == 1
        )


def test_daily_flow_rejects_cutoff_after_batch_cutoff(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-cutoff.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    rows = [_bar("000300.SH", day, Decimal("100") + Decimal(day)) for day in range(1, 27)]
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/daily-bars.csv",
            "license_note": "licensed",
        },
        rows,
    )
    strategy = repository.create_strategy(owner_id, {"name": "trend", "parameters": {}})
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    with pytest.raises(StateConflictError):
        repository.run_daily_flow(
            owner_id,
            {
                "data_batch_id": batch["batch_id"],
                "strategy_version_id": strategy["strategy_version_id"],
                "cost_config_id": "cost_v1",
                "rule_config_id": "rule_v1",
                "as_of_date": "2026-09-25",
                "information_cutoff_at": "2026-09-27T18:00:00+00:00",
                "idempotency_key": "daily-late-cutoff",
            },
        )


def test_confirmed_daily_plan_records_manual_fill_and_reconciled_ledger(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-fill.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    rows = [
        bar
        for day in range(1, 27)
        for bar in (
            _bar("600000.SH", day, Decimal("10") + Decimal(day) / 10),
            _bar("000300.SH", day, Decimal("100") + Decimal(day)),
        )
    ]
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/daily-bars.csv",
            "license_note": "licensed",
        },
        rows,
    )
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    run = repository.run_daily_flow(
        owner_id,
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "as_of_date": "2026-09-25",
            "information_cutoff_at": "2026-09-25T18:00:00+00:00",
            "idempotency_key": "daily-manual-fill",
        },
    )
    plan = repository.list_plans(date(2026, 9, 26), 1, 50)["items"][0]
    confirmed = repository.confirm_plan(plan["plan_id"], owner_id, "CONFIRM", 1, "reviewed")
    execution = repository.create_execution(
        {
            "plan_id": plan["plan_id"],
            "execution_type": "MANUAL_ENTRY",
            "executed_at": datetime(2026, 9, 26, 15, 0, tzinfo=UTC).isoformat(),
            "quantity": 40,
            "price": "10.00",
            "commission": "5.00",
            "stamp_tax": "0.00",
            "transfer_fee": "0.01",
            "other_fee": "0.00",
            "unfilled_quantity": 60,
            "note": "manual fill",
            "idempotency_key": "execution-key",
        },
        owner_id,
    )
    replay = repository.create_execution(
        {
            "plan_id": plan["plan_id"],
            "execution_type": "MANUAL_ENTRY",
            "executed_at": datetime(2026, 9, 26, 15, 0, tzinfo=UTC).isoformat(),
            "quantity": 40,
            "price": "10.00",
            "commission": "5.00",
            "stamp_tax": "0.00",
            "transfer_fee": "0.01",
            "other_fee": "0.00",
            "unfilled_quantity": 60,
            "note": "manual fill",
            "idempotency_key": "execution-key",
        },
        owner_id,
    )
    completion = repository.create_execution(
        {
            "plan_id": plan["plan_id"],
            "execution_type": "MANUAL_ENTRY",
            "executed_at": datetime(2026, 9, 26, 15, 1, tzinfo=UTC).isoformat(),
            "quantity": 60,
            "price": "10.00",
            "commission": "5.00",
            "stamp_tax": "0.00",
            "transfer_fee": "0.01",
            "other_fee": "0.00",
            "unfilled_quantity": 0,
            "note": "manual completion",
            "idempotency_key": "execution-completion-key",
        },
        owner_id,
    )

    assert run["status"] == "SUCCEEDED"
    assert confirmed is not None and confirmed["status"] == "CONFIRMED"
    assert confirmed["version"] == 2
    assert execution is not None and execution["status"] == "PARTIALLY_FILLED"
    assert replay is not None and replay["replayed"] is True
    assert replay["execution_id"] == execution["execution_id"]
    assert completion is not None and completion["status"] == "FILLED"
    with engine.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(models.LedgerEntry)).scalar() == 3
        )
        assert (
            connection.execute(select(func.count()).select_from(models.PortfolioSnapshot)).scalar()
            == 2
        )
        latest = connection.execute(
            select(models.PortfolioSnapshot).order_by(models.PortfolioSnapshot.trade_date.desc())
        ).first()
        assert latest is not None
        assert latest.equity == latest.cash + latest.market_value
        assert latest.cash == Decimal("18989.98")
        position = connection.execute(select(models.PositionSnapshot)).first()
        assert position is not None
        assert position.quantity == 100
        assert position.available_quantity == 0
