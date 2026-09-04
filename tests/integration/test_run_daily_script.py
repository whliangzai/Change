import csv
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.domain.data.importer import AuthorizedFileImporter
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from scripts import run_daily


def _write_bars(path) -> None:
    fields = [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adjustment_factor",
        "available_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for day in range(1, 27):
            trade_date = date(2026, 9, day)
            for symbol, close in (
                ("600000.SH", Decimal("10") + Decimal(day) / 10),
                ("000300.SH", Decimal("100") + Decimal(day)),
            ):
                writer.writerow(
                    {
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
                )


def _configure(monkeypatch, database_url: str, source, owner_id, strategy_id) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DAILY_OWNER_ID", str(owner_id))
    monkeypatch.setenv("DAILY_STRATEGY_VERSION_ID", str(strategy_id))
    monkeypatch.setenv("DAILY_SOURCE_PATH", str(source))


def test_run_daily_script_imports_and_persists_real_data_idempotently(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.csv"
    _write_bars(source)
    database_url = f"sqlite:///{tmp_path / 'daily-script.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    _configure(monkeypatch, database_url, source, owner_id, strategy["strategy_version_id"])
    monkeypatch.setattr(
        run_daily.sys,
        "argv",
        ["run_daily", "--business-date", "2026-09-25"],
    )

    assert run_daily.main() == 0
    first_output = capsys.readouterr().out
    assert '"status": "succeeded"' in first_output
    assert run_daily.main() == 0
    second_output = capsys.readouterr().out
    assert '"replayed": true' in second_output

    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(models.DataBatch)).scalar() == 1
        assert connection.execute(select(func.count()).select_from(models.DailyBar)).scalar() == 52
        assert (
            connection.execute(select(func.count()).select_from(models.BacktestRun)).scalar() == 1
        )
        assert connection.execute(select(func.count()).select_from(models.JobRun)).scalar() == 1


def test_run_daily_script_persists_failure_then_reuses_batch_on_restart(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.csv"
    _write_bars(source)
    database_url = f"sqlite:///{tmp_path / 'daily-retry.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    missing_strategy_id = uuid4()
    _configure(monkeypatch, database_url, source, owner_id, missing_strategy_id)
    monkeypatch.setattr(
        run_daily.sys,
        "argv",
        ["run_daily", "--business-date", "2026-09-25"],
    )

    assert run_daily.main() == 1
    assert "daily report failed" in capsys.readouterr().err

    repository = SqlAlchemyResearchRepository.from_engine(engine)
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    monkeypatch.setenv("DAILY_STRATEGY_VERSION_ID", strategy["strategy_version_id"])

    assert run_daily.main() == 0
    assert '"status": "succeeded"' in capsys.readouterr().out

    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(models.DataBatch)).scalar() == 1
        assert connection.execute(select(func.count()).select_from(models.DailyBar)).scalar() == 52
    with Session(engine) as session:
        job = session.scalar(select(models.JobRun))
        assert job is not None
        assert job.status == "succeeded"
        assert job.attempt == 2


def test_run_daily_script_can_reuse_an_explicit_persisted_batch_without_source(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.csv"
    _write_bars(source)
    database_url = f"sqlite:///{tmp_path / 'explicit-batch.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    imported = AuthorizedFileImporter().import_file(source)
    batch = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "licensed",
            "content_hash": imported.content_hash,
            "file_hash": imported.file_hash,
        },
        list(imported.rows),
    )
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"min_amount_ratio": "0"}},
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DAILY_OWNER_ID", str(owner_id))
    monkeypatch.setenv("DAILY_DATA_BATCH_ID", batch["batch_id"])
    monkeypatch.setenv("DAILY_STRATEGY_VERSION_ID", strategy["strategy_version_id"])
    monkeypatch.delenv("DAILY_SOURCE_PATH", raising=False)
    monkeypatch.setattr(
        run_daily.sys,
        "argv",
        ["run_daily", "--business-date", "2026-09-25"],
    )

    assert run_daily.main() == 0
    assert '"status": "succeeded"' in capsys.readouterr().out
