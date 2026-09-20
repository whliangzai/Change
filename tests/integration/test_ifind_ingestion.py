from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select

from app.application.ifind_ingestion import IFindIngestionService
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository


class RecordingClient:
    def __init__(self, *, complete: bool = True, limit_evidence: bool = True) -> None:
        self.calls: list[str] = []
        self.complete = complete
        self.limit_evidence = limit_evidence

    def fetch_dataset(self, dataset, codes=None, **kwargs):
        self.calls.append(dataset)
        trade_date = kwargs.get("start_date", date(2025, 1, 2))
        codes = list(codes or ["000001.SZ", "600000.SH", "000300.SH", "000001.SH"])
        if dataset == "trading_calendar":
            return [{"tradeDate": trade_date.isoformat(), "isOpen": True, "exchange": "SSE"}]
        if dataset == "security_master":
            rows = []
            for code in codes:
                rows.append(
                    {
                        "thscode": code,
                        "listedDate": "2010-01-01",
                        "delistedDate": None,
                        "board": "MAIN",
                    }
                )
            return rows
        if dataset == "daily_bars":
            return [
                {
                    "thscode": code,
                    "tradeDate": trade_date.isoformat(),
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                    "amount": 1000,
                    **(
                        {
                            "openLimitUp": False,
                            "openLimitDown": False,
                            "closeLimitUp": False,
                            "closeLimitDown": False,
                        }
                        if self.limit_evidence
                        else {}
                    ),
                }
                for code in codes
            ]
        if dataset == "adjustment_factors":
            return (
                []
                if not self.complete
                else [
                    {"thscode": code, "tradeDate": trade_date.isoformat(), "af": 1}
                    for code in codes
                ]
            )
        if dataset == "historical_status":
            return [
                {
                    "thscode": code,
                    "tradeDate": trade_date.isoformat(),
                    "isST": False,
                    "isSuspended": False,
                    "isDelistingArrange": False,
                }
                for code in codes
            ]
        if dataset == "industry_membership":
            return [
                {"thscode": code, "industryCode": "C20", "inDate": "2010-01-01", "outDate": None}
                for code in codes
            ]
        return []


def _service(tmp_path: Path, *, complete: bool = True, limit_evidence: bool = True):
    engine = create_engine(f"sqlite:///{tmp_path / 'ifind.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    client = RecordingClient(complete=complete, limit_evidence=limit_evidence)
    service = IFindIngestionService(client, repository, artifact_root=tmp_path / "artifacts")
    return service, client, engine


def test_ifind_import_normalizes_and_persists_provenance(tmp_path: Path) -> None:
    service, client, engine = _service(tmp_path)
    result = service.import_business_date("2025-01-02", "pilot")

    assert result["quality_status"] == "AVAILABLE"
    assert result["record_count"] == 4
    assert client.calls[:2] == ["trading_calendar", "security_master"]
    assert result["quality_summary"]["provenance"]["mapping_version"] == "v1"
    assert result["standardized_manifest"]["layer"] == "standardized"
    assert result["information_cutoff_at"].endswith("T10:30:00+00:00")
    assert result["available_at"].endswith("T10:30:00+00:00")
    with engine.connect() as connection:
        limits = connection.execute(
            select(models.DailyBar.limit_up, models.DailyBar.limit_down)
        ).all()
        assert limits
        assert all(limit_up is False and limit_down is False for limit_up, limit_down in limits)
        assert connection.execute(select(models.AdjustmentFactor)).fetchall()
        assert connection.execute(select(models.TradeCalendar)).fetchall()
        assert connection.execute(select(models.IndustryMembershipHistory)).fetchall()


def test_ifind_missing_adjustment_data_is_unavailable(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, complete=False)
    result = service.import_business_date(date(2025, 1, 2), "pilot")

    assert result["quality_status"] == "UNAVAILABLE"
    assert result["quality_summary"]["blocking"] is True
    assert any("adjustment factors" in issue for issue in result["quality_summary"]["issues"])


def test_ifind_missing_explicit_limit_evidence_is_unavailable(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, limit_evidence=False)
    result = service.import_business_date(date(2025, 1, 2), "pilot")

    assert result["quality_status"] == "UNAVAILABLE"
    assert any("price limit" in str(issue) for issue in result["quality_summary"]["issues"])
