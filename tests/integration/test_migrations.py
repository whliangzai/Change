from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import Numeric

from alembic import command
from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base

EXPECTED_TABLES = {
    "user_account",
    "role",
    "user_role",
    "data_source",
    "data_batch",
    "trade_calendar",
    "security",
    "security_status_history",
    "industry_membership_history",
    "daily_bar",
    "adjustment_factor",
    "rule_config_version",
    "cost_config_version",
    "strategy_version",
    "signal_snapshot",
    "backtest_run",
    "backtest_series_point",
    "run_stage",
    "portfolio_snapshot",
    "position_snapshot",
    "order_plan",
    "execution_record",
    "ledger_entry",
    "performance_metric",
    "report_artifact",
    "daily_report",
    "job_run",
    "idempotency_record",
    "auth_session",
    "audit_event",
    "system_alert",
}


def test_initial_migration_creates_versioned_market_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    engine = create_engine(database_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    assert "uq_data_batch_source_dataset_date_version" in {
        item["name"] for item in inspector.get_unique_constraints("data_batch")
    }
    daily_bar_columns = {item["name"]: item["type"] for item in inspector.get_columns("daily_bar")}
    assert isinstance(daily_bar_columns["raw_close"], Numeric)
    assert isinstance(daily_bar_columns["adjusted_close"], Numeric)
    assert {"limit_up", "limit_down"} <= set(daily_bar_columns)
    assert {
        "open_limit_up",
        "open_limit_down",
        "close_limit_up",
        "close_limit_down",
    } <= set(daily_bar_columns)
    assert inspector.get_pk_constraint("daily_bar")["constrained_columns"] == [
        "security_id",
        "trade_date",
        "data_batch_id",
    ]
    assert inspector.get_pk_constraint("adjustment_factor")["constrained_columns"] == [
        "security_id",
        "effective_date",
        "factor_type",
        "data_batch_id",
    ]
    assert inspector.get_pk_constraint("security_status_history")["constrained_columns"] == [
        "security_id",
        "effective_date",
        "source_batch_id",
    ]
    assert "observed_at" in {
        item["name"] for item in inspector.get_columns("security_status_history")
    }
    assert "observed_at" in {
        item["name"] for item in inspector.get_columns("industry_membership_history")
    }
    strategy_columns = {item["name"] for item in inspector.get_columns("strategy_version")}
    assert "strategy_type" in strategy_columns
    metric_columns = {item["name"] for item in inspector.get_columns("performance_metric")}
    assert "series_code" in metric_columns
    execution_columns = {item["name"] for item in inspector.get_columns("execution_record")}
    assert "idempotency_key" in execution_columns
    assert "uq_execution_record_plan_idempotency_key" in {
        item["name"] for item in inspector.get_indexes("execution_record")
    }
    assert "uq_job_run_active_task_key" in {
        item["name"] for item in inspector.get_indexes("job_run")
    }
    assert Base.metadata.tables["data_batch"].c.available_at.type.timezone is True
    assert Base.metadata.tables["audit_event"].c.occurred_at.type.timezone is True


def test_upgrade_from_legacy_market_primary_keys_preserves_rows_and_allows_new_versions(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-market.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "0005_multi_strategy_engine_v2")

    source_id, first_batch_id, second_batch_id, security_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with Session(engine) as session:
        session.add(models.DataSource(id=source_id, name="legacy", kind="HTTP", enabled=True))
        for batch_id, version in ((first_batch_id, "v1"), (second_batch_id, "v2")):
            session.add(
                models.DataBatch(
                    id=batch_id,
                    source_id=source_id,
                    dataset_type="DAILY_BAR",
                    as_of_date=date(2025, 1, 2),
                    available_at=datetime(2025, 1, 31, 10, 30, tzinfo=UTC),
                    information_cutoff_at=datetime(2025, 1, 31, 10, 30, tzinfo=UTC),
                    version=version,
                    status="AVAILABLE",
                    quality_summary={},
                    content_hash=version * 32,
                    start_date=date(2025, 1, 2),
                    end_date=date(2025, 1, 31),
                    record_count=1,
                )
            )
        session.add(
            models.Security(
                id=security_id,
                symbol="600000.SH",
                exchange="SSE",
                security_type="COMMON",
                list_date=date(2000, 1, 1),
            )
        )
        session.commit()

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in (
            "daily_bar",
            "adjustment_factor",
            "security_status_history",
            "industry_membership_history",
        ):
            connection.exec_driver_sql(f"DROP TABLE {table}")
        connection.exec_driver_sql(
            """CREATE TABLE daily_bar (
                security_id CHAR(32) NOT NULL,
                trade_date DATE NOT NULL,
                raw_open NUMERIC NOT NULL, raw_high NUMERIC NOT NULL,
                raw_low NUMERIC NOT NULL, raw_close NUMERIC NOT NULL,
                adjusted_open NUMERIC NOT NULL, adjusted_high NUMERIC NOT NULL,
                adjusted_low NUMERIC NOT NULL, adjusted_close NUMERIC NOT NULL,
                volume NUMERIC NOT NULL, amount NUMERIC NOT NULL,
                adjust_factor NUMERIC NOT NULL, available_at DATETIME,
                limit_up BOOLEAN, limit_down BOOLEAN,
                data_batch_id CHAR(32) NOT NULL,
                CONSTRAINT pk_daily_bar PRIMARY KEY (security_id, trade_date)
            )"""
        )
        connection.exec_driver_sql(
            """CREATE TABLE adjustment_factor (
                security_id CHAR(32) NOT NULL, effective_date DATE NOT NULL,
                factor NUMERIC NOT NULL, factor_type VARCHAR(16) NOT NULL,
                data_batch_id CHAR(32) NOT NULL,
                CONSTRAINT pk_adjustment_factor
                PRIMARY KEY (security_id, effective_date, factor_type)
            )"""
        )
        connection.exec_driver_sql(
            """CREATE TABLE security_status_history (
                security_id CHAR(32) NOT NULL, effective_date DATE NOT NULL,
                is_st BOOLEAN NOT NULL, is_suspended BOOLEAN NOT NULL,
                is_delist_period BOOLEAN NOT NULL, board VARCHAR(16) NOT NULL,
                source_batch_id CHAR(32) NOT NULL,
                CONSTRAINT pk_security_status_history PRIMARY KEY (security_id, effective_date)
            )"""
        )
        connection.exec_driver_sql(
            """CREATE TABLE industry_membership_history (
                security_id CHAR(32) NOT NULL, industry_code VARCHAR(32) NOT NULL,
                effective_from DATE NOT NULL, effective_to DATE,
                source_batch_id CHAR(32) NOT NULL,
                CONSTRAINT pk_industry_membership_history
                PRIMARY KEY (security_id, industry_code, effective_from)
            )"""
        )
        identifiers = {
            "security": security_id.hex,
            "batch": first_batch_id.hex,
        }
        connection.execute(
            text(
                """INSERT INTO daily_bar VALUES
                (:security, '2025-01-02', 10, 11, 9, 10, 10, 11, 9, 10,
                 100, 1000, 1, '2025-01-02 10:30:00', 0, 0, :batch)"""
            ),
            identifiers,
        )
        connection.execute(
            text(
                "INSERT INTO adjustment_factor VALUES "
                "(:security, '2025-01-02', 1, 'DEFAULT', :batch)"
            ),
            identifiers,
        )
        connection.execute(
            text(
                "INSERT INTO security_status_history VALUES "
                "(:security, '2025-01-02', 0, 0, 0, 'MAIN', :batch)"
            ),
            identifiers,
        )
        connection.execute(
            text(
                "INSERT INTO security_status_history VALUES "
                "(:security, '2025-01-03', 0, 1, 0, 'MAIN', :batch)"
            ),
            identifiers,
        )
        connection.execute(
            text(
                "INSERT INTO industry_membership_history VALUES "
                "(:security, 'BANK', '2020-01-01', NULL, :batch)"
            ),
            identifiers,
        )
        connection.commit()

    command.upgrade(config, "head")

    with engine.connect() as connection:
        status_observed = connection.execute(
            text(
                "SELECT effective_date, observed_at FROM security_status_history "
                "ORDER BY effective_date"
            )
        ).all()
        assert str(status_observed[0].observed_at).startswith("2025-01-02 10:30:00")
        assert str(status_observed[1].observed_at).startswith("2025-01-31 10:30:00")
        industry_observed = connection.execute(
            text("SELECT observed_at FROM industry_membership_history")
        ).scalar_one()
        assert str(industry_observed).startswith("2025-01-31 10:30:00")

    with engine.begin() as connection:
        second = {"security": security_id.hex, "batch": second_batch_id.hex}
        connection.execute(
            text(
                """INSERT INTO daily_bar (
                    security_id, trade_date, raw_open, raw_high, raw_low, raw_close,
                    adjusted_open, adjusted_high, adjusted_low, adjusted_close,
                    volume, amount, adjust_factor, available_at,
                    open_limit_up, open_limit_down, close_limit_up, close_limit_down,
                    limit_up, limit_down, data_batch_id
                ) VALUES (
                    :security, '2025-01-02', 10, 11, 9, 10, 10, 11, 9, 10,
                    100, 1000, 1, '2025-01-02 10:30:00', 0, 0, 0, 0, 0, 0, :batch
                )"""
            ),
            second,
        )
        connection.execute(
            text(
                "INSERT INTO adjustment_factor VALUES "
                "(:security, '2025-01-02', 1, 'DEFAULT', :batch)"
            ),
            second,
        )
        connection.execute(
            text(
                "INSERT INTO security_status_history ("
                "security_id, effective_date, is_st, is_suspended, "
                "is_delist_period, board, source_batch_id, observed_at"
                ") VALUES ("
                ":security, '2025-01-02', 0, 0, 0, 'MAIN', :batch, "
                "'2025-01-02 10:30:00'"
                ")"
            ),
            second,
        )
        connection.execute(
            text(
                "INSERT INTO industry_membership_history ("
                "security_id, industry_code, effective_from, effective_to, "
                "source_batch_id, observed_at"
                ") VALUES ("
                ":security, 'BANK', '2020-01-01', NULL, :batch, "
                "'2025-01-02 10:30:00'"
                ")"
            ),
            second,
        )
        expected_counts = {
            "daily_bar": 2,
            "adjustment_factor": 2,
            "security_status_history": 3,
            "industry_membership_history": 2,
        }
        for table, expected in expected_counts.items():
            assert (
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == expected
            )
