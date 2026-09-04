import json
from uuid import uuid4

import pandas as pd
from sqlalchemy import create_engine, select

from app.infrastructure.db import models
from scripts import import_data


def test_import_script_persists_database_batch_when_configured(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.json"
    source.write_text(
        json.dumps(
            [
                {
                    "symbol": "600000.SH",
                    "trade_date": "2026-09-03",
                    "open": "10.00",
                    "high": "10.20",
                    "low": "9.90",
                    "close": "10.10",
                    "volume": "10000",
                    "amount": "101000.00",
                    "adjustment_factor": "1.0",
                    "available_at": "2026-09-03T18:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    database_url = f"sqlite:///{tmp_path / 'script.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("IMPORT_OWNER_ID", str(uuid4()))
    monkeypatch.setattr(
        import_data.sys,
        "argv",
        ["import_data", str(source), "--data-root", str(tmp_path / "artifacts")],
    )

    assert import_data.main() == 0
    assert "batch_status=AVAILABLE" in capsys.readouterr().out
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(select(models.DataBatch)).first() is not None
        assert connection.execute(select(models.DailyBar)).first() is not None


def test_import_script_persists_authorized_parquet_directly_to_database(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "trade_date": "2026-09-03",
                "open": "10.00",
                "high": "10.20",
                "low": "9.90",
                "close": "10.10",
                "volume": "10000",
                "amount": "101000.00",
                "adjustment_factor": "1.0",
                "available_at": "2026-09-03T18:00:00+00:00",
            }
        ]
    ).to_parquet(source)
    database_url = f"sqlite:///{tmp_path / 'parquet-script.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("IMPORT_OWNER_ID", str(uuid4()))
    monkeypatch.setattr(
        import_data.sys,
        "argv",
        ["import_data", str(source), "--data-root", str(tmp_path / "artifacts")],
    )

    assert import_data.main() == 0
    assert "batch_status=AVAILABLE" in capsys.readouterr().out
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(select(models.DailyBar)).first() is not None
