from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.ifind_ingestion import IFindIngestionService
from app.application.tushare_ingestion import TushareIngestionService
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository

FIRST_DATE = date(2025, 1, 2)
CODES = ("000001.SZ", "600000.SH", "000300.SH", "000001.SH")


def _index(day: date) -> int:
    return (day - FIRST_DATE).days


def _calendar(day: date, *, ifind: bool) -> list[dict[str, Any]]:
    dates = [FIRST_DATE - timedelta(days=70) + timedelta(days=index) for index in range(70)]
    dates.extend(FIRST_DATE + timedelta(days=index) for index in range(_index(day) + 1))
    if ifind:
        return [
            {"tradeDate": value.isoformat(), "isOpen": True, "exchange": "SSE"} for value in dates
        ]
    return [
        {"cal_date": value.strftime("%Y%m%d"), "is_open": 1, "exchange": "SSE"} for value in dates
    ]


class HistoricalTushareClient:
    def preflight(self) -> dict[str, str]:
        return {"status": "ok"}

    def fetch_dataset(
        self, dataset: str, codes: object = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        day = kwargs.get("start_date", FIRST_DATE)
        requested = list(codes or CODES)
        if dataset == "trading_calendar":
            return _calendar(day, ifind=False)
        if dataset in {"security_master", "index_master"}:
            return [
                {
                    "ts_code": code,
                    "list_date": "20100101",
                    "market": "主板" if code not in {"000300.SH", "000001.SH"} else "SSE",
                }
                for code in requested
            ]
        close = 10 + _index(day) / 10
        if dataset in {"daily_bars", "index_daily_bars"}:
            return [
                {
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "vol": 1_000_000,
                    "amount": 30_000_000,
                }
                for code in requested
            ]
        if dataset == "adjustment_factors":
            return [{"ts_code": code, "adj_factor": 1} for code in requested]
        if dataset == "price_limits":
            return [
                {
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "up_limit": close + 5,
                    "down_limit": close - 5,
                }
                for code in CODES[:2]
            ]
        if dataset in {"historical_status", "suspensions"}:
            return []
        if dataset == "industry_membership":
            return [
                {"ts_code": code, "l3_code": "BANK", "in_date": "20100101"} for code in requested
            ]
        return []


class HistoricalIFindClient:
    def fetch_dataset(
        self, dataset: str, codes: object = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        day = kwargs.get("start_date", FIRST_DATE)
        requested = list(codes or CODES)
        if dataset == "trading_calendar":
            return _calendar(day, ifind=True)
        if dataset == "security_master":
            return [
                {
                    "thscode": code,
                    "listedDate": "2010-01-01",
                    "delistedDate": None,
                    "board": "MAIN",
                }
                for code in requested
            ]
        close = 10 + _index(day) / 10
        if dataset == "daily_bars":
            return [
                {
                    "thscode": code,
                    "tradeDate": day.isoformat(),
                    "open": close - 0.02,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 1_000_000,
                    "amount": 30_000_000,
                    "openLimitUp": False,
                    "openLimitDown": False,
                    "closeLimitUp": False,
                    "closeLimitDown": False,
                }
                for code in requested
            ]
        if dataset == "adjustment_factors":
            return [{"thscode": code, "tradeDate": day.isoformat(), "af": 1} for code in requested]
        if dataset == "historical_status":
            return [
                {
                    "thscode": code,
                    "tradeDate": day.isoformat(),
                    "isST": False,
                    "isSuspended": False,
                    "isDelistingArrange": False,
                }
                for code in requested
            ]
        if dataset == "industry_membership":
            return [
                {
                    "thscode": code,
                    "industryCode": "BANK",
                    "inDate": "2010-01-01",
                    "outDate": None,
                }
                for code in requested
            ]
        return []


def _service(
    provider: str,
    repository: SqlAlchemyResearchRepository,
    artifact_root: Path,
) -> object:
    if provider == "tushare":
        return TushareIngestionService(
            HistoricalTushareClient(), repository, artifact_root=artifact_root
        )
    return IFindIngestionService(HistoricalIFindClient(), repository, artifact_root=artifact_root)


@pytest.mark.parametrize("provider", ["tushare", "ifind"])
def test_provider_daily_batches_form_reproducible_multi_period_backtest(
    tmp_path: Path, provider: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{provider}.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    service = _service(provider, repository, tmp_path / f"{provider}-artifacts")

    batches = [
        service.import_business_date(FIRST_DATE + timedelta(days=index), "pilot")
        for index in range(61)
    ]
    assert all(
        batch["quality_status"] in {"AVAILABLE", "WARNING_AVAILABLE"} for batch in batches
    ), [batch["quality_summary"] for batch in batches if batch["quality_status"] != "AVAILABLE"]

    owner_id = UUID(int=0)
    strategy = repository.create_strategy(
        owner_id,
        {
            "name": f"{provider}-closure",
            "change_reason": "provider closure acceptance",
            "strategy_type": "STRONG_TREND",
            "parameters": {"min_amount_ratio": "0"},
        },
    )
    repository.submit_strategy(
        strategy["strategy_version_id"],
        owner_id,
        "PUBLISH",
        "provider closure reviewed",
        allow_reviewer=True,
    )
    batch_ids = [str(batch["batch_id"]) for batch in batches]
    payload = {
        "data_batch_id": batch_ids[0],
        "data_batch_ids": batch_ids,
        "strategy_version_id": strategy["strategy_version_id"],
        "cost_config_id": "cost_v1",
        "rule_config_id": "rule_v2",
        "start_date": (FIRST_DATE + timedelta(days=20)).isoformat(),
        "end_date": (FIRST_DATE + timedelta(days=60)).isoformat(),
        "train_end": (FIRST_DATE + timedelta(days=35)).isoformat(),
        "valid_end": (FIRST_DATE + timedelta(days=45)).isoformat(),
        "oos_start": (FIRST_DATE + timedelta(days=46)).isoformat(),
        "benchmark_symbol": "000300.SH",
        "initial_equity": "20000.00",
        "information_cutoff_at": batches[-1]["information_cutoff_at"],
        "mode": "BACKTEST",
    }

    validation = repository.validate_backtest(owner_id, payload)
    assert validation["available"] is True, validation
    assert validation["data_batch_ids"] == batch_ids
    run = repository.create_run(owner_id, payload)
    completed = repository.execute_run(run["run_id"], owner_id)
    trades = repository.get_run_child(run["run_id"], owner_id, "trades")

    assert completed is not None and completed["status"] == "SUCCEEDED"
    assert trades is not None and trades["total"] > 0
    with Session(engine) as session:
        persisted = session.scalar(
            select(models.BacktestRun).where(models.BacktestRun.run_no == run["run_id"])
        )
        assert persisted is not None
        snapshot = persisted.config_snapshot
        assert len(snapshot["data_batches"]) == 61
        assert snapshot["data_batch_set_hash"]
        assert snapshot["status_history_hash"]
        assert snapshot["security_master_hash"]
        assert snapshot["trading_calendar_hash"]
        assert snapshot["cost_config_hash"]
        assert snapshot["cost_config"]["slippage_buy"] == "0.00200000"

    if provider == "ifind":
        for field, changed in (
            ("list_date", date(2011, 1, 1)),
            ("delist_date", date(2025, 12, 31)),
            ("security_type", "ETF"),
        ):
            master_bound = repository.create_run(owner_id, payload)
            with Session(engine) as session:
                security = session.scalar(
                    select(models.Security).where(models.Security.symbol == "600000.SH")
                )
                assert security is not None
                original = getattr(security, field)
                setattr(security, field, changed)
                session.commit()
            rejected_master = repository.execute_run(master_bound["run_id"], owner_id)
            assert rejected_master is not None
            assert rejected_master["status"] == "UNAVAILABLE"
            with Session(engine) as session:
                security = session.scalar(
                    select(models.Security).where(models.Security.symbol == "600000.SH")
                )
                assert security is not None
                setattr(security, field, original)
                session.commit()

        calendar_bound = repository.create_run(owner_id, payload)
        with Session(engine) as session:
            calendar = session.get(
                models.TradeCalendar,
                ("SSE", FIRST_DATE + timedelta(days=30)),
            )
            assert calendar is not None
            calendar.is_open = False
            session.commit()
        rejected_calendar = repository.execute_run(calendar_bound["run_id"], owner_id)
        assert rejected_calendar is not None
        assert rejected_calendar["status"] == "UNAVAILABLE"
        with Session(engine) as session:
            calendar = session.get(
                models.TradeCalendar,
                ("SSE", FIRST_DATE + timedelta(days=30)),
            )
            assert calendar is not None
            calendar.is_open = True
            session.commit()

        status_bound = repository.create_run(owner_id, payload)
        with Session(engine) as session:
            status = session.scalar(
                select(models.SecurityStatusHistory).order_by(
                    models.SecurityStatusHistory.effective_date.desc()
                )
            )
            assert status is not None
            status.is_st = not status.is_st
            session.commit()
        rejected_status = repository.execute_run(status_bound["run_id"], owner_id)
        assert rejected_status is not None
        assert rejected_status["status"] == "UNAVAILABLE"

        with Session(engine) as session:
            status = session.scalar(
                select(models.SecurityStatusHistory).order_by(
                    models.SecurityStatusHistory.effective_date.desc()
                )
            )
            assert status is not None
            status.is_st = not status.is_st
            session.commit()
        cost_bound = repository.create_run(owner_id, payload)
        with Session(engine) as session:
            cost = session.scalar(
                select(models.CostConfigVersion).where(
                    models.CostConfigVersion.version == "cost_v1"
                )
            )
            assert cost is not None
            cost.slippage_buy = cost.slippage_buy + Decimal("0.001")
            session.commit()
        rejected_cost = repository.execute_run(cost_bound["run_id"], owner_id)
        assert rejected_cost is not None
        assert rejected_cost["status"] == "UNAVAILABLE"

        assert (
            repository.validate_backtest(
                owner_id,
                {
                    **payload,
                    "data_batch_id": batch_ids[-1],
                    "data_batch_ids": list(reversed(batch_ids)),
                },
            )["available"]
            is False
        )
        assert (
            repository.validate_backtest(
                owner_id,
                {
                    **payload,
                    "data_batch_ids": batch_ids[:30] + batch_ids[31:],
                },
            )["available"]
            is False
        )

        with Session(engine) as session:
            batch = session.get(models.DataBatch, UUID(batch_ids[10]))
            assert batch is not None
            original_summary = dict(batch.quality_summary)
            batch.quality_summary = {**original_summary, "mapping_version": "incompatible-v2"}
            session.commit()
        assert repository.validate_backtest(owner_id, payload)["available"] is False
        with Session(engine) as session:
            batch = session.get(models.DataBatch, UUID(batch_ids[10]))
            assert batch is not None
            batch.quality_summary = original_summary
            session.commit()

        with Session(engine) as session:
            batch = session.get(models.DataBatch, UUID(batch_ids[11]))
            assert batch is not None
            original_source_id = batch.source_id
            source = models.DataSource(name="incompatible-source", kind="HTTP", enabled=True)
            session.add(source)
            session.flush()
            batch.source_id = source.id
            session.commit()
        assert repository.validate_backtest(owner_id, payload)["available"] is False
        with Session(engine) as session:
            batch = session.get(models.DataBatch, UUID(batch_ids[11]))
            assert batch is not None
            batch.source_id = original_source_id
            session.commit()

        with Session(engine) as session:
            original_bar = session.scalar(
                select(models.DailyBar).where(models.DailyBar.data_batch_id == UUID(batch_ids[30]))
            )
            assert original_bar is not None
            original_security_id = original_bar.security_id
            original_trade_date = original_bar.trade_date
            overlapping_bar = models.DailyBar(
                security_id=original_security_id,
                trade_date=original_trade_date,
                raw_open=original_bar.raw_open,
                raw_high=original_bar.raw_high,
                raw_low=original_bar.raw_low,
                raw_close=original_bar.raw_close,
                adjusted_open=original_bar.adjusted_open,
                adjusted_high=original_bar.adjusted_high,
                adjusted_low=original_bar.adjusted_low,
                adjusted_close=original_bar.adjusted_close,
                volume=original_bar.volume,
                amount=original_bar.amount,
                adjust_factor=original_bar.adjust_factor,
                available_at=original_bar.available_at,
                open_limit_up=original_bar.open_limit_up,
                open_limit_down=original_bar.open_limit_down,
                close_limit_up=original_bar.close_limit_up,
                close_limit_down=original_bar.close_limit_down,
                limit_up=original_bar.limit_up,
                limit_down=original_bar.limit_down,
                data_batch_id=UUID(batch_ids[31]),
            )
            session.add(overlapping_bar)
            session.commit()
        assert repository.validate_backtest(owner_id, payload)["available"] is False
        with Session(engine) as session:
            overlapping_bar = session.get(
                models.DailyBar,
                (
                    original_security_id,
                    original_trade_date,
                    UUID(batch_ids[31]),
                ),
            )
            assert overlapping_bar is not None
            session.delete(overlapping_bar)
            session.commit()

        with Session(engine) as session:
            first_batch = session.get(models.DataBatch, UUID(batch_ids[30]))
            assert first_batch is not None
            session.add_all(
                [
                    models.DataBatch(
                        source_id=first_batch.source_id,
                        owner_id=first_batch.owner_id,
                        dataset_type=first_batch.dataset_type,
                        as_of_date=first_batch.as_of_date,
                        available_at=first_batch.available_at,
                        information_cutoff_at=first_batch.information_cutoff_at,
                        version="competing-correction",
                        status="AVAILABLE",
                        quality_summary=first_batch.quality_summary,
                        content_hash="f" * 64,
                        file_hash="e" * 64,
                        start_date=first_batch.start_date,
                        end_date=first_batch.end_date,
                        record_count=first_batch.record_count,
                    ),
                    models.DataBatch(
                        source_id=first_batch.source_id,
                        owner_id=str(UUID(int=1)),
                        dataset_type=first_batch.dataset_type,
                        as_of_date=first_batch.as_of_date,
                        available_at=first_batch.available_at,
                        information_cutoff_at=first_batch.information_cutoff_at,
                        version="other-owner-correction",
                        status="AVAILABLE",
                        quality_summary=first_batch.quality_summary,
                        content_hash="d" * 64,
                        file_hash="c" * 64,
                        start_date=first_batch.start_date,
                        end_date=first_batch.end_date,
                        record_count=first_batch.record_count,
                    ),
                ]
            )
            session.commit()
        assert repository.validate_backtest(owner_id, payload)["available"] is True

        with Session(engine) as session:
            membership = session.scalar(
                select(models.IndustryMembershipHistory).where(
                    models.IndustryMembershipHistory.source_batch_id == UUID(batch_ids[30])
                )
            )
            assert membership is not None
            session.add(
                models.IndustryMembershipHistory(
                    security_id=membership.security_id,
                    industry_code="CONFLICTING_INDUSTRY",
                    effective_from=membership.effective_from,
                    effective_to=membership.effective_to,
                    source_batch_id=membership.source_batch_id,
                    observed_at=membership.observed_at,
                )
            )
            session.commit()
        conflict_validation = repository.validate_backtest(owner_id, payload)
        assert conflict_validation == {
            "available": False,
            "reason": "industry history has an unresolved selected-batch tie",
        }
