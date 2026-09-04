from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository


def test_export_record_survives_repository_restart_and_is_owner_scoped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'exports.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    other_owner_id = uuid4()
    run_id = uuid4()
    with Session(engine) as session:
        session.add(
            models.BacktestRun(
                id=run_id,
                run_no="run-export",
                data_batch_id=uuid4(),
                strategy_version_id=uuid4(),
                cost_config_id=uuid4(),
                rule_config_id=uuid4(),
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 3),
                train_end=date(2026, 9, 1),
                valid_end=date(2026, 9, 2),
                oos_start=date(2026, 9, 3),
                benchmark_symbol="000300.SH",
                secondary_benchmark_symbol="000001.SH",
                universe_benchmark_enabled=True,
                execution_price_mode="NEXT_OPEN_ADJUSTED",
                initial_equity=Decimal("20000.00"),
                config_snapshot={},
                status="SUCCEEDED",
                result_usable=True,
                created_by=owner_id,
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            models.ReportArtifact(
                id=uuid4(),
                run_id=run_id,
                report_type="BACKTEST",
                file_path="reports/run-export.json",
                content_hash="a" * 64,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    first = SqlAlchemyResearchRepository.from_engine(engine)
    created = first.create_export(str(run_id), owner_id)
    loaded = SqlAlchemyResearchRepository.from_engine(engine).get_export(
        created["export_id"], owner_id
    )

    assert created["status"] == "READY"
    assert loaded is not None
    assert loaded["file_path"] == "reports/run-export.json"
    assert (
        SqlAlchemyResearchRepository.from_engine(engine).get_export(
            created["export_id"], other_owner_id
        )
        is None
    )
