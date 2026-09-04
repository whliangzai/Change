from datetime import date
from uuid import uuid4

import pytest

from scripts import run_backtest, run_daily


@pytest.mark.parametrize(
    ("module", "message"),
    [
        (run_backtest, "backtest service is not configured"),
        (run_daily, "daily service is not configured"),
    ],
)
def test_non_dry_run_without_service_fails_closed(monkeypatch, capsys, module, message) -> None:
    monkeypatch.setattr(module.sys, "argv", [module.__name__, "--business-date", "2026-09-03"])

    assert module.main() == 2
    assert message in capsys.readouterr().err


def test_daily_runtime_database_initialization_fails_closed(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///unavailable.db")
    monkeypatch.setenv("DAILY_OWNER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("DAILY_SOURCE_PATH", "missing.csv")
    monkeypatch.setenv("DAILY_STRATEGY_VERSION_ID", "00000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(
        run_daily,
        "make_engine",
        lambda _: (_ for _ in ()).throw(ConnectionError("database unavailable")),
    )
    monkeypatch.setattr(
        run_daily.sys, "argv", [run_daily.__name__, "--business-date", "2026-09-03"]
    )

    assert run_daily.main() == 1
    assert "daily report failed" in capsys.readouterr().err


@pytest.mark.parametrize(("created", "audit_count"), [(True, 1), (False, 0)])
def test_daily_cli_initializes_local_default_versions_before_running(
    monkeypatch, created, audit_count
) -> None:
    calls: list[str] = []
    events: list[object] = []

    class Repository:
        def initialize_local_default_versions(self) -> dict[str, object]:
            calls.append("initialized")
            return {"created": created, "created_versions": ["cost_v1", "rule_v1"]}

    class AuditWriter:
        def append(self, event) -> None:
            events.append(event)

    repository = Repository()
    monkeypatch.setattr(run_daily, "make_engine", lambda _: object())
    monkeypatch.setattr(
        run_daily.SqlAlchemyResearchRepository,
        "from_engine",
        lambda *_args, **_kwargs: repository,
    )
    monkeypatch.setattr(run_daily.SqlAlchemyJobRunStore, "from_engine", lambda _: object())
    monkeypatch.setattr(run_daily.SqlAlchemyAuditWriter, "from_engine", lambda _: AuditWriter())
    monkeypatch.setattr(
        run_daily,
        "run_daily_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop after initialization")),
    )
    monkeypatch.setattr(
        run_daily.sys,
        "argv",
        [
            "run_daily",
            "--database-url",
            "sqlite:///daily.db",
            "--owner-id",
            str(uuid4()),
            "--source-path",
            "bars.csv",
            "--strategy-version-id",
            str(uuid4()),
        ],
    )

    assert run_daily.main() == 1
    assert calls == ["initialized"]
    assert len(events) == audit_count
    if events:
        assert events[0].action == "LOCAL_DEFAULT_CONFIG_INITIALIZE"
        assert events[0].after_summary == {
            "created": True,
            "created_versions": ["cost_v1", "rule_v1"],
        }


def test_backtest_non_dry_run_executes_injected_service(monkeypatch, capsys) -> None:
    monkeypatch.setattr(run_backtest.sys, "argv", ["run_backtest", "--business-date", "2026-09-03"])
    calls: list[date] = []

    result = run_backtest.main(service=lambda *, business_date: calls.append(business_date))

    assert result == 0
    assert calls == [date(2026, 9, 3)]
    assert "succeeded" in capsys.readouterr().out


def test_backtest_dry_run_requires_durable_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_backtest.sys,
        "argv",
        ["run_backtest", "--business-date", "2026-09-03", "--dry-run"],
    )

    assert run_backtest.main() == 2
    assert "DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize(("created", "audit_count"), [(True, 1), (False, 0)])
def test_backtest_cli_initializes_local_default_versions_before_validation(
    monkeypatch, created, audit_count
) -> None:
    calls: list[str] = []
    events: list[object] = []

    class Repository:
        def initialize_local_default_versions(self) -> dict[str, object]:
            calls.append("initialized")
            return {"created": created, "created_versions": ["cost_v1", "rule_v1"]}

        def validate_backtest(self, _owner_id, _payload) -> dict[str, object]:
            raise RuntimeError("stop after initialization")

    class AuditWriter:
        def append(self, event) -> None:
            events.append(event)

    repository = Repository()
    monkeypatch.setattr(run_backtest, "make_engine", lambda _: object())
    monkeypatch.setattr(
        run_backtest.SqlAlchemyResearchRepository,
        "from_engine",
        lambda *_args, **_kwargs: repository,
    )
    monkeypatch.setattr(run_backtest.SqlAlchemyAuditWriter, "from_engine", lambda _: AuditWriter())
    monkeypatch.setattr(
        run_backtest.sys,
        "argv",
        [
            "run_backtest",
            "--database-url",
            "sqlite:///backtest.db",
            "--owner-id",
            str(uuid4()),
            "--data-batch-id",
            str(uuid4()),
            "--strategy-version-id",
            str(uuid4()),
            "--start-date",
            "2026-09-01",
            "--train-end",
            "2026-09-01",
            "--valid-end",
            "2026-09-02",
            "--oos-start",
            "2026-09-03",
            "--end-date",
            "2026-09-03",
            "--dry-run",
        ],
    )

    assert run_backtest.main() == 1
    assert calls == ["initialized"]
    assert len(events) == audit_count
    if events:
        assert events[0].action == "LOCAL_DEFAULT_CONFIG_INITIALIZE"
        assert events[0].after_summary == {
            "created": True,
            "created_versions": ["cost_v1", "rule_v1"],
        }


def test_backtest_cli_executes_against_persisted_repository_and_is_replayable(
    monkeypatch, capsys, tmp_path
) -> None:
    from sqlalchemy import create_engine, func, select

    from app.infrastructure.db import models
    from app.infrastructure.db.base import Base
    from app.infrastructure.repositories.research import SqlAlchemyResearchRepository

    owner_id = uuid4()
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    rows = [
        {
            "symbol": symbol,
            "trade_date": f"2026-09-{day:02d}",
            "open": "10.00" if symbol == "600000.SH" else "100.00",
            "high": "10.20" if symbol == "600000.SH" else "100.20",
            "low": "9.90" if symbol == "600000.SH" else "99.90",
            "close": "10.10" if symbol == "600000.SH" else "100.10",
            "volume": "10000",
            "amount": "101000.00",
            "adjustment_factor": "1.0",
            "available_at": f"2026-09-{day:02d}T18:00:00+00:00",
        }
        for day in range(1, 4)
        for symbol in ("600000.SH", "000300.SH")
    ]
    batch = repository.import_daily_bars(
        owner_id,
        {"source_name": "licensed-csv", "data_type": "DAILY_BAR", "file_location": "bars.csv"},
        rows,
    )
    strategy = repository.create_strategy(
        owner_id, {"name": "trend", "parameters": {}, "change_reason": "initial"}
    )
    repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    assert repository.dependencies_available(
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "owner_id": str(owner_id),
        }
    )
    settings = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'cli.db'}",
        "BACKTEST_OWNER_ID": str(owner_id),
        "BACKTEST_DATA_BATCH_ID": batch["batch_id"],
        "BACKTEST_STRATEGY_VERSION_ID": strategy["strategy_version_id"],
        "BACKTEST_START_DATE": "2026-09-01",
        "BACKTEST_END_DATE": "2026-09-03",
        "BACKTEST_TRAIN_END": "2026-09-01",
        "BACKTEST_VALID_END": "2026-09-02",
        "BACKTEST_OOS_START": "2026-09-03",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        run_backtest.sys,
        "argv",
        ["run_backtest", "--business-date", "2026-09-03", "--scope", "cli-test"],
    )

    monkeypatch.setattr(
        run_backtest.sys,
        "argv",
        ["run_backtest", "--business-date", "2026-09-03", "--scope", "cli-test", "--dry-run"],
    )
    assert run_backtest.main() == 0
    assert '"status": "dry-run"' in capsys.readouterr().out
    assert (
        engine.connect().execute(select(func.count()).select_from(models.BacktestRun)).scalar_one()
        == 0
    )

    monkeypatch.setattr(
        run_backtest.sys,
        "argv",
        ["run_backtest", "--business-date", "2026-09-03", "--scope", "cli-test"],
    )
    assert run_backtest.main() == 0
    first_output = capsys.readouterr().out
    assert '"status": "succeeded"' in first_output
    assert (
        engine.connect().execute(select(func.count()).select_from(models.BacktestRun)).scalar_one()
        == 1
    )

    assert run_backtest.main() == 0
    second_output = capsys.readouterr().out
    assert '"replayed": true' in second_output
    assert (
        engine.connect().execute(select(func.count()).select_from(models.BacktestRun)).scalar_one()
        == 1
    )


def test_daily_non_dry_run_executes_injected_service(monkeypatch, capsys) -> None:
    monkeypatch.setattr(run_daily.sys, "argv", ["run_daily", "--business-date", "2026-09-03"])
    calls: list[date] = []

    result = run_daily.main(service=lambda *, business_date: calls.append(business_date))

    assert result == 0
    assert calls == [date(2026, 9, 3)]
    assert "succeeded" in capsys.readouterr().out
