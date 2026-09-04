from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select

from app.core.errors import StateConflictError
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository


def _row(symbol: str = "600000.SH", trade_date: str = "2026-09-03") -> dict[str, str]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "open": "10.00",
        "high": "10.20",
        "low": "9.90",
        "close": "10.10",
        "volume": "10000",
        "amount": "101000.00",
        "adjustment_factor": "1.000000",
        "available_at": "2026-09-03T18:00:00+00:00",
    }


def test_import_persists_quality_batch_security_and_bars(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'import.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)

    result = repository.import_daily_bars(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/bars.csv",
            "license_note": "licensed",
        },
        [_row(), _row("000001.SZ")],
    )

    assert result["quality_status"] == "AVAILABLE"
    assert result["record_count"] == 2
    restarted = SqlAlchemyResearchRepository.from_engine(engine)
    pool = restarted.list_pool(date(2026, 9, 3), None, 1, 50)
    assert pool["total"] == 2
    batches = restarted.list_batches(owner_id, None, 1, 50)
    assert batches["total"] == 1
    listed = batches["items"][0]
    assert listed["batch_id"] == result["batch_id"]
    assert listed["start_date"] == "2026-09-03"
    assert listed["end_date"] == "2026-09-03"
    assert listed["record_count"] == 2
    assert listed["file_hash"]
    assert listed["version"]
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(models.DailyBar)).scalar() == 2


def test_import_fails_closed_on_duplicate_rows_and_does_not_publish_bars(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid-import.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    result = repository.import_daily_bars(
        uuid4(),
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/bars.csv",
            "license_note": "licensed",
        },
        [_row(), _row()],
    )

    assert result["quality_status"] == "UNAVAILABLE"
    assert result["record_count"] == 0
    assert result["quality_summary"]["blocking"] is True
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(models.DailyBar)).scalar() == 0


def test_reimporting_the_same_file_fails_with_a_conflict(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-import.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    owner_id = uuid4()
    payload = {
        "source_name": "licensed-csv",
        "data_type": "DAILY_BAR",
        "file_location": "data/bars.csv",
        "license_note": "licensed",
    }

    repository.import_daily_bars(owner_id, payload, [_row()])

    with pytest.raises(StateConflictError):
        repository.import_daily_bars(owner_id, payload, [_row()])
