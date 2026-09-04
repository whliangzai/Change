from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect
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
    execution_columns = {item["name"] for item in inspector.get_columns("execution_record")}
    assert "idempotency_key" in execution_columns
    assert "uq_execution_record_plan_idempotency_key" in {
        item["name"] for item in inspector.get_indexes("execution_record")
    }
    assert Base.metadata.tables["data_batch"].c.available_at.type.timezone is True
    assert Base.metadata.tables["audit_event"].c.occurred_at.type.timezone is True
