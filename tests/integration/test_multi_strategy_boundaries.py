from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.core.errors import StateConflictError
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository


def test_missing_explicit_price_limit_evidence_remains_unknown() -> None:
    bar = SimpleNamespace(limit_up=None, limit_down=None, raw_open=Decimal("11"))
    status = SimpleNamespace(is_st=False, board="MAIN")
    previous = SimpleNamespace(raw_close=Decimal("10"))

    assert SqlAlchemyResearchRepository._limit_state(bar, status, previous) == (
        False,
        False,
        False,
    )


def test_same_effective_date_dimensions_use_selected_batch_order_and_reject_ties() -> None:
    security_id = uuid4()
    older_batch_id = uuid4()
    newer_batch_id = uuid4()
    effective_date = date(2025, 1, 2)
    priority = {older_batch_id: 0, newer_batch_id: 1}
    statuses = [
        models.SecurityStatusHistory(
            security_id=security_id,
            effective_date=effective_date,
            is_st=True,
            is_suspended=False,
            is_delist_period=False,
            board="MAIN",
            source_batch_id=older_batch_id,
            observed_at=datetime(2025, 1, 2, 10, 30, tzinfo=UTC),
        ),
        models.SecurityStatusHistory(
            security_id=security_id,
            effective_date=effective_date,
            is_st=False,
            is_suspended=False,
            is_delist_period=False,
            board="MAIN",
            source_batch_id=newer_batch_id,
            observed_at=datetime(2025, 1, 4, 10, 30, tzinfo=UTC),
        ),
    ]
    status_on_first_date = SqlAlchemyResearchRepository._status_as_of(
        statuses,
        date(2025, 1, 3),
        datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        priority,
    )
    status_after_correction = SqlAlchemyResearchRepository._status_as_of(
        statuses,
        date(2025, 1, 4),
        datetime(2025, 1, 4, 23, 59, tzinfo=UTC),
        priority,
    )
    assert status_on_first_date is not None and status_on_first_date.is_st is True
    assert status_after_correction is not None and status_after_correction.is_st is False

    memberships = [
        models.IndustryMembershipHistory(
            security_id=security_id,
            industry_code="BANK",
            effective_from=effective_date,
            effective_to=None,
            source_batch_id=older_batch_id,
            observed_at=datetime(2025, 1, 2, 10, 30, tzinfo=UTC),
        ),
        models.IndustryMembershipHistory(
            security_id=security_id,
            industry_code="TECH",
            effective_from=effective_date,
            effective_to=None,
            source_batch_id=newer_batch_id,
            observed_at=datetime(2025, 1, 4, 10, 30, tzinfo=UTC),
        ),
    ]
    industry_on_first_date, conflict = SqlAlchemyResearchRepository._industry_as_of(
        memberships,
        date(2025, 1, 3),
        datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
        priority,
    )
    assert conflict is None
    assert industry_on_first_date is not None
    assert industry_on_first_date.industry_code == "BANK"
    industry_after_correction, conflict = SqlAlchemyResearchRepository._industry_as_of(
        memberships,
        date(2025, 1, 4),
        datetime(2025, 1, 4, 23, 59, tzinfo=UTC),
        priority,
    )
    assert conflict is None
    assert industry_after_correction is not None
    assert industry_after_correction.industry_code == "TECH"

    memberships.append(
        models.IndustryMembershipHistory(
            security_id=security_id,
            industry_code="ENERGY",
            effective_from=effective_date,
            effective_to=None,
            source_batch_id=newer_batch_id,
            observed_at=datetime(2025, 1, 4, 10, 30, tzinfo=UTC),
        )
    )
    _, conflict = SqlAlchemyResearchRepository._industry_as_of(
        memberships,
        date(2025, 1, 4),
        datetime(2025, 1, 4, 23, 59, tzinfo=UTC),
        priority,
    )
    assert conflict == "industry history has an unresolved selected-batch tie"


def test_ended_newer_industry_version_does_not_fall_back_to_older_open_record() -> None:
    security_id = uuid4()
    older_batch_id = uuid4()
    newer_batch_id = uuid4()
    memberships = [
        models.IndustryMembershipHistory(
            security_id=security_id,
            industry_code="BANK",
            effective_from=date(2020, 1, 1),
            effective_to=None,
            source_batch_id=older_batch_id,
            observed_at=datetime(2020, 1, 2, 10, 30, tzinfo=UTC),
        ),
        models.IndustryMembershipHistory(
            security_id=security_id,
            industry_code="BANK",
            effective_from=date(2020, 1, 1),
            effective_to=date(2024, 12, 31),
            source_batch_id=newer_batch_id,
            observed_at=datetime(2024, 12, 31, 10, 30, tzinfo=UTC),
        ),
    ]

    membership, conflict = SqlAlchemyResearchRepository._industry_as_of(
        memberships,
        date(2025, 1, 2),
        datetime(2025, 1, 2, 23, 59, tzinfo=UTC),
        {older_batch_id: 0, newer_batch_id: 1},
    )

    assert conflict is None
    assert membership is None


def test_ma_trend_is_explicitly_rejected_by_daily_flow(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-boundary.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    batch = repository.create_batch(
        owner_id,
        {
            "source_name": "authorized-test",
            "data_type": "DAILY_BAR",
            "file_location": "authorized.csv",
            "license_note": "test",
            "date_to": "2026-09-01",
        },
    )
    strategy = repository.create_strategy(
        owner_id,
        {
            "name": "ma",
            "change_reason": "backtest only",
            "strategy_type": "MA_TREND",
            "parameters": {},
        },
    )
    repository.submit_strategy(
        strategy["strategy_version_id"],
        owner_id,
        "PUBLISH",
        "reviewed for backtest only",
        allow_reviewer=True,
    )

    with pytest.raises(StateConflictError, match="not approved for daily flow"):
        repository.run_daily_flow(
            owner_id,
            {
                "data_batch_id": batch["batch_id"],
                "strategy_version_id": strategy["strategy_version_id"],
                "cost_config_id": "cost_v1",
                "rule_config_id": "rule_v1",
                "as_of_date": "2026-09-01",
            },
        )


def test_persisted_ma_backtest_writes_engine_v2_series_and_segment_metrics(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'ma-backtest.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    owner_id = uuid4()
    first = date(2026, 1, 1)
    rows = []
    for index in range(66):
        trade_date = first + timedelta(days=index)
        for symbol, close in (
            ("600000.SH", Decimal("10") + Decimal(index) / 10),
            ("510300.SH", Decimal("5") + Decimal(index)),
            ("000300.SH", Decimal("100") + Decimal(index)),
        ):
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "open": str(close - Decimal("0.02")),
                    "high": str(close + Decimal("0.10")),
                    "low": str(close - Decimal("0.10")),
                    "close": str(close),
                    "volume": "100000",
                    "amount": "30000000",
                    "adjustment_factor": "1",
                    "available_at": f"{trade_date.isoformat()}T18:00:00+00:00",
                    "limit_up": False,
                    "limit_down": False,
                }
            )
    with Session(engine) as session:
        session.add_all(
            models.TradeCalendar(
                exchange="SSE",
                trade_date=first + timedelta(days=index),
                is_open=True,
            )
            for index in range(66)
        )
        session.commit()
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "authorized-test",
            "data_type": "DAILY_BAR",
            "file_location": "ma-bars.csv",
            "license_note": "test",
        },
        rows,
    )
    with Session(engine) as session:
        etf = session.scalar(select(models.Security).where(models.Security.symbol == "510300.SH"))
        assert etf is not None
        etf.security_type = "ETF"
        session.commit()
    strategy = repository.create_strategy(
        owner_id,
        {
            "name": "ma",
            "change_reason": "backtest",
            "strategy_type": "MA_TREND",
            "parameters": {"max_holding_days": 2},
        },
    )
    repository.submit_strategy(
        strategy["strategy_version_id"],
        owner_id,
        "PUBLISH",
        "reviewed",
        allow_reviewer=True,
    )
    run_payload = {
        "data_batch_id": batch["batch_id"],
        "strategy_version_id": strategy["strategy_version_id"],
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v2",
        "start_date": (first + timedelta(days=59)).isoformat(),
        "end_date": (first + timedelta(days=65)).isoformat(),
        "train_end": (first + timedelta(days=61)).isoformat(),
        "valid_end": (first + timedelta(days=63)).isoformat(),
        "oos_start": (first + timedelta(days=64)).isoformat(),
        "benchmark_symbol": "000300.SH",
        "initial_equity": "20000.00",
        "mode": "BACKTEST",
    }
    with pytest.raises(StateConflictError, match="engine-v2 rule version"):
        repository.create_run(owner_id, {**run_payload, "rule_config_id": "rule_v1"})
    run = repository.create_run(owner_id, run_payload)
    completed = repository.execute_run(run["run_id"], owner_id)
    report = repository.get_run_child(run["run_id"], owner_id, "report")

    assert completed is not None and completed["status"] == "SUCCEEDED"
    assert report is not None
    assert report["engine_version"] == "engine-v2"
    assert report["strategy_type"] == "MA_TREND"
    assert set(report["series"]) == {
        "STRATEGY",
        "BENCHMARK_PRIMARY",
        "UNIVERSE_EQUAL_WEIGHT",
    }
    assert "STRATEGY:FULL" in report["segment_metrics"]
    assert "UNIVERSE_EQUAL_WEIGHT:FULL" in report["segment_metrics"]
    universe_points = report["series"]["UNIVERSE_EQUAL_WEIGHT"]["points"]
    expected_universe = Decimal("20000") * Decimal("16.5") / Decimal("15.9")
    assert abs(Decimal(universe_points[-1]["value"]) - expected_universe) < Decimal("1e-20")

    mismatched = repository.create_run(owner_id, run_payload)
    with Session(engine) as session:
        stored = session.scalar(
            select(models.BacktestRun).where(models.BacktestRun.run_no == mismatched["run_id"])
        )
        assert stored is not None
        stored.config_snapshot = {
            **stored.config_snapshot,
            "strategy_implementation_version": "ma-trend-future",
        }
        session.commit()
    blocked = repository.execute_run(mismatched["run_id"], owner_id)
    assert blocked is not None
    assert blocked["status"] == "UNAVAILABLE"
    assert "implementation is unavailable" in blocked["result_summary"]["reason"]

    missing_benchmark = repository.create_run(owner_id, run_payload)
    missing_date = first + timedelta(days=62)
    with Session(engine) as session:
        benchmark = session.scalar(
            select(models.Security).where(models.Security.symbol == "000300.SH")
        )
        assert benchmark is not None
        session.execute(
            delete(models.DailyBar).where(
                models.DailyBar.security_id == benchmark.id,
                models.DailyBar.trade_date == missing_date,
            )
        )
        session.commit()
    unavailable = repository.execute_run(missing_benchmark["run_id"], owner_id)
    assert unavailable is not None
    assert unavailable["status"] == "UNAVAILABLE"
    assert missing_date.isoformat() in unavailable["result_summary"]["reason"]
