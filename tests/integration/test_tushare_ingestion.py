from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select

from app.application.tushare_ingestion import TushareIngestionError, TushareIngestionService
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.infrastructure.tushare.http_client import TushareHttpClient


class TushareRecordingClient:
    def __init__(self, complete: bool = True) -> None:
        self.complete = complete

    def preflight(self):
        return {"status": "ok"}

    def fetch_dataset(self, dataset, codes=None, **kwargs):
        day = kwargs.get("start_date", date(2025, 1, 2))
        codes = list(codes or ["000001.SZ", "600000.SH", "000300.SH", "000001.SH"])
        if dataset == "trading_calendar":
            return [{"cal_date": day.strftime("%Y%m%d"), "is_open": 1, "exchange": "SSE"}]
        if dataset == "security_master":
            return [{"ts_code": code, "list_date": "20100101", "market": "MAIN"} for code in codes]
        if dataset == "index_master":
            return [{"ts_code": code, "list_date": "20050101", "market": "SSE"} for code in codes]
        if dataset in {"daily_bars", "index_daily_bars"}:
            return [
                {
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 100,
                    "amount": 1000,
                }
                for code in codes
            ]
        if dataset == "adjustment_factors":
            return (
                [] if not self.complete else [{"ts_code": code, "adj_factor": 1} for code in codes]
            )
        if dataset == "historical_status":
            return [{"ts_code": "000001.SZ", "trade_date": day.strftime("%Y%m%d"), "type": "ST"}]
        if dataset == "suspensions":
            return []
        if dataset == "industry_membership":
            return [
                {"ts_code": code, "l3_code": "801010.SI", "in_date": "20100101"} for code in codes
            ]
        return []


class FullScopeTushareRecordingClient(TushareRecordingClient):
    """Record the stock_basic status requests needed to avoid survivorship bias."""

    def __init__(self) -> None:
        super().__init__()
        self.security_master_statuses: list[str | None] = []

    def fetch_dataset(self, dataset, codes=None, **kwargs):
        if dataset == "security_master":
            list_status = (kwargs.get("extra") or {}).get("list_status")
            self.security_master_statuses.append(list_status)
            active = [
                {"ts_code": "000001.SZ", "list_date": "19910403", "market": "主板"},
                {"ts_code": "600000.SH", "list_date": "19991110", "market": "主板"},
            ]
            delisted = [
                {
                    "ts_code": "600001.SH",
                    "list_date": "20100101",
                    "delist_date": "20200103",
                    "market": "主板",
                }
            ]
            paused = [
                {"ts_code": "600002.SH", "list_date": "20100101", "market": "主板"}
            ]
            return (
                active
                if list_status == "L"
                else delisted
                if list_status == "D"
                else paused
                if list_status == "P"
                else []
            )
        return super().fetch_dataset(dataset, codes, **kwargs)


class SuspendedFullScopeTushareRecordingClient(TushareRecordingClient):
    """Model Tushare's absence of daily and adjustment rows during a suspension."""

    def __init__(self, *, include_suspended_bar: bool = False) -> None:
        super().__init__()
        self.include_suspended_bar = include_suspended_bar

    def fetch_dataset(self, dataset, codes=None, **kwargs):
        if dataset == "security_master":
            list_status = (kwargs.get("extra") or {}).get("list_status")
            return (
                [
                    {"ts_code": "000001.SZ", "list_date": "19910403", "market": "主板"},
                    {"ts_code": "600000.SH", "list_date": "19991110", "market": "主板"},
                ]
                if list_status == "L"
                else []
            )
        if dataset == "suspensions":
            day = kwargs.get("start_date", date(2025, 1, 2))
            return [
                {
                    "ts_code": "600000.SH",
                    "trade_date": day.strftime("%Y%m%d"),
                    "suspend_type": "S",
                }
            ]
        if dataset == "historical_status":
            return []
        rows = super().fetch_dataset(dataset, codes, **kwargs)
        if dataset in {"daily_bars", "adjustment_factors"} and not self.include_suspended_bar:
            return [row for row in rows if row["ts_code"] != "600000.SH"]
        return rows


class AKShareRecordingClient:
    def __init__(self, different: bool) -> None:
        self.different = different

    def fetch_calendar(self, business_date):
        return [{"trade_date": business_date.isoformat(), "is_open": True}]

    def fetch_daily_bars(self, codes, business_date):
        return [
            {
                "symbol": code,
                "trade_date": business_date.isoformat(),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 99 if self.different else 10.5,
                "volume": 100,
                "amount": 1000,
            }
            for code in codes
        ]


def _service(tmp_path: Path, *, complete: bool = True, different: bool = False):
    engine = create_engine(f"sqlite:///{tmp_path / 'tushare.db'}")
    Base.metadata.create_all(engine)
    service = TushareIngestionService(
        TushareRecordingClient(complete),
        SqlAlchemyResearchRepository.from_engine(engine),
        validation_client=AKShareRecordingClient(different),
        artifact_root=tmp_path / "artifacts",
        price_tolerance=Decimal("0"),
    )
    return service, engine


def test_tushare_pilot_publishes_with_akshare_warning(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, different=True)
    result = service.import_business_date("2025-01-02", "pilot")
    assert result["source_name"] == "tushare_http"
    assert result["quality_status"] == "WARNING_AVAILABLE"
    assert result["record_count"] == 4
    assert result["quality_summary"]["provenance"]["mapping_version"] == "v1"
    assert result["quality_summary"]["warnings"][0]["kind"] == "AKSHARE_DIFFERENCE"
    with engine.connect() as connection:
        assert connection.execute(select(models.DailyBar)).fetchall()


def test_tushare_missing_required_history_blocks_publication(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, complete=False)
    result = service.import_business_date(date(2025, 1, 2), "pilot")
    assert result["quality_status"] == "UNAVAILABLE"
    assert any("adjustment factors" in issue for issue in result["quality_summary"]["issues"])


def test_tushare_ingests_official_shape_recordings_without_index_adjustment_factor(
    tmp_path: Path,
) -> None:
    recordings = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "tushare" / "pilot_api_recordings.json"
        ).read_text(encoding="utf-8")
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_name = json.loads(request.read())["api_name"]
        calls.append(api_name)
        return httpx.Response(200, json=recordings[api_name])

    engine = create_engine(f"sqlite:///{tmp_path / 'recorded-tushare.db'}")
    Base.metadata.create_all(engine)
    client = TushareHttpClient(
        "recording-token",
        rate_limit_per_minute=10_000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    service = TushareIngestionService(
        client,
        repository,
        validation_client=AKShareRecordingClient(False),
        artifact_root=tmp_path / "recorded-artifacts",
    )
    result = service.import_business_date("2025-01-02", "pilot")

    assert result["quality_status"] == "AVAILABLE", result["quality_summary"]
    assert result["record_count"] == 4
    assert "stock_st" in calls and "index_member_all" in calls and "index_daily" in calls
    with engine.connect() as connection:
        statuses = connection.execute(select(models.SecurityStatusHistory.is_st)).scalars().all()
        assert any(statuses)
        assert len(connection.execute(select(models.IndustryMembershipHistory)).fetchall()) == 2
        boards = connection.execute(select(models.SecurityStatusHistory.board)).scalars().all()
        assert boards.count("MAIN") == 2
    pool = repository.list_pool(date(2025, 1, 2), "ELIGIBLE", 1, 100)
    assert "600000.SH" in {row["symbol"] for row in pool["items"]}


def test_tushare_full_scope_includes_delisted_securities_only_before_delisting(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'full-scope-tushare.db'}")
    Base.metadata.create_all(engine)
    client = FullScopeTushareRecordingClient()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    service = TushareIngestionService(
        client,
        repository,
        validation_client=AKShareRecordingClient(False),
        artifact_root=tmp_path / "full-scope-artifacts",
    )

    before_delisting = service.import_business_date("2019-12-31", "full")
    assert before_delisting["quality_status"] == "AVAILABLE"
    assert before_delisting["record_count"] == 6
    assert client.security_master_statuses == ["L", "D", "P"]
    eligible_before = repository.list_pool(date(2019, 12, 31), "ELIGIBLE", 1, 100)
    assert {"600001.SH", "600002.SH"}.issubset(
        {row["symbol"] for row in eligible_before["items"]}
    )

    after_delisting = service.import_business_date("2020-01-03", "full")
    assert after_delisting["quality_status"] == "AVAILABLE"
    assert after_delisting["record_count"] == 5
    eligible_after = repository.list_pool(date(2020, 1, 3), "ELIGIBLE", 1, 100)
    assert "600001.SH" not in {row["symbol"] for row in eligible_after["items"]}


def test_tushare_full_scope_persists_suspension_without_daily_bar(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'suspended-full-scope-tushare.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    service = TushareIngestionService(
        SuspendedFullScopeTushareRecordingClient(),
        repository,
        validation_client=AKShareRecordingClient(False),
        artifact_root=tmp_path / "suspended-full-scope-artifacts",
    )

    result = service.import_business_date("2025-01-02", "full")

    assert result["quality_status"] == "AVAILABLE", result["quality_summary"]
    assert result["record_count"] == 3
    with engine.connect() as connection:
        status = connection.execute(
            select(models.SecurityStatusHistory.is_suspended)
            .join(models.Security, models.SecurityStatusHistory.security_id == models.Security.id)
            .where(models.Security.symbol == "600000.SH")
        ).scalar_one()
        assert status is True
        daily_symbols = connection.execute(
            select(models.Security.symbol)
            .join(models.DailyBar, models.DailyBar.security_id == models.Security.id)
            .where(models.DailyBar.trade_date == date(2025, 1, 2))
        ).scalars().all()
        assert "600000.SH" not in daily_symbols


def test_tushare_full_scope_keeps_intraday_suspension_bar_but_excludes_pool(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'intraday-suspension-tushare.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    service = TushareIngestionService(
        SuspendedFullScopeTushareRecordingClient(include_suspended_bar=True),
        repository,
        validation_client=AKShareRecordingClient(False),
        artifact_root=tmp_path / "intraday-suspension-artifacts",
    )

    result = service.import_business_date("2025-01-02", "full")

    assert result["quality_status"] == "AVAILABLE", result["quality_summary"]
    assert result["record_count"] == 4
    with engine.connect() as connection:
        daily_symbols = connection.execute(
            select(models.Security.symbol)
            .join(models.DailyBar, models.DailyBar.security_id == models.Security.id)
            .where(models.DailyBar.trade_date == date(2025, 1, 2))
        ).scalars().all()
        assert "600000.SH" in daily_symbols
    eligible = repository.list_pool(date(2025, 1, 2), "ELIGIBLE", 1, 100)
    assert "600000.SH" not in {row["symbol"] for row in eligible["items"]}


def test_tushare_recorded_missing_adjustment_history_blocks_before_publication(
    tmp_path: Path,
) -> None:
    recordings = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "tushare" / "pilot_api_recordings.json"
        ).read_text(encoding="utf-8")
    )
    recordings = deepcopy(recordings)
    recordings["adj_factor"]["data"]["items"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=recordings[json.loads(request.read())["api_name"]])

    engine = create_engine(f"sqlite:///{tmp_path / 'missing-history.db'}")
    Base.metadata.create_all(engine)
    service = TushareIngestionService(
        TushareHttpClient(
            "recording-token",
            rate_limit_per_minute=10_000,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        SqlAlchemyResearchRepository.from_engine(engine),
        artifact_root=tmp_path / "missing-history-artifacts",
    )
    with pytest.raises(TushareIngestionError, match="adjustment_factors"):
        service.import_business_date("2025-01-02", "pilot")
