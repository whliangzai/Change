"""Add strategy kinds, proven limit state, series, and series-scoped metrics."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0005_multi_strategy_engine_v2"
down_revision = "0004_append_job_attempts"
branch_labels = None
depends_on = None

_OLD_METRIC_UQ = "uq_performance_metric_run_segment_code"
_NEW_METRIC_UQ = "uq_performance_metric_run_series_segment_code"


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "strategy_type" not in _columns("strategy_version"):
        op.add_column(
            "strategy_version",
            sa.Column(
                "strategy_type",
                sa.String(length=32),
                nullable=False,
                server_default="STRONG_TREND",
            ),
        )
    op.execute(
        sa.text(
            "UPDATE strategy_version SET strategy_type = 'STRONG_TREND' "
            "WHERE strategy_type IS NULL OR strategy_type = ''"
        )
    )

    daily_columns = _columns("daily_bar")
    if "limit_up" not in daily_columns:
        op.add_column("daily_bar", sa.Column("limit_up", sa.Boolean(), nullable=True))
    if "limit_down" not in daily_columns:
        op.add_column("daily_bar", sa.Column("limit_down", sa.Boolean(), nullable=True))

    metric_columns = _columns("performance_metric")
    if "series_code" not in metric_columns:
        op.add_column(
            "performance_metric",
            sa.Column(
                "series_code", sa.String(length=32), nullable=False, server_default="STRATEGY"
            ),
        )
    op.execute(
        sa.text(
            "UPDATE performance_metric SET series_code = 'STRATEGY' "
            "WHERE series_code IS NULL OR series_code = ''"
        )
    )
    constraints = {
        item["name"] for item in inspect(bind).get_unique_constraints("performance_metric")
    }
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("performance_metric") as batch:
            if _OLD_METRIC_UQ in constraints:
                batch.drop_constraint(_OLD_METRIC_UQ, type_="unique")
            if _NEW_METRIC_UQ not in constraints:
                batch.create_unique_constraint(
                    _NEW_METRIC_UQ,
                    ["run_id", "series_code", "segment", "metric_code"],
                )
    else:
        if _OLD_METRIC_UQ in constraints:
            op.drop_constraint(_OLD_METRIC_UQ, "performance_metric", type_="unique")
        if _NEW_METRIC_UQ not in constraints:
            op.create_unique_constraint(
                _NEW_METRIC_UQ,
                "performance_metric",
                ["run_id", "series_code", "segment", "metric_code"],
            )

    if "backtest_series_point" not in tables:
        op.create_table(
            "backtest_series_point",
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("series_code", sa.String(length=32), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("value", sa.Numeric(24, 10), nullable=False),
            sa.Column(
                "availability",
                sa.String(length=32),
                nullable=False,
                server_default="AVAILABLE",
            ),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["backtest_run.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "series_code",
                "trade_date",
                name="uq_backtest_series_run_code_date",
            ),
        )
        op.create_index(
            "ix_backtest_series_run_date",
            "backtest_series_point",
            ["run_id", "trade_date"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "backtest_series_point" in inspect(bind).get_table_names():
        op.drop_table("backtest_series_point")
    constraints = {
        item["name"] for item in inspect(bind).get_unique_constraints("performance_metric")
    }
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("performance_metric") as batch:
            if _NEW_METRIC_UQ in constraints:
                batch.drop_constraint(_NEW_METRIC_UQ, type_="unique")
            if _OLD_METRIC_UQ not in constraints:
                batch.create_unique_constraint(_OLD_METRIC_UQ, ["run_id", "segment", "metric_code"])
            if "series_code" in _columns("performance_metric"):
                batch.drop_column("series_code")
    else:
        if _NEW_METRIC_UQ in constraints:
            op.drop_constraint(_NEW_METRIC_UQ, "performance_metric", type_="unique")
        if _OLD_METRIC_UQ not in constraints:
            op.create_unique_constraint(
                _OLD_METRIC_UQ,
                "performance_metric",
                ["run_id", "segment", "metric_code"],
            )
        if "series_code" in _columns("performance_metric"):
            op.drop_column("performance_metric", "series_code")
    daily_columns = _columns("daily_bar")
    if "limit_down" in daily_columns:
        op.drop_column("daily_bar", "limit_down")
    if "limit_up" in daily_columns:
        op.drop_column("daily_bar", "limit_up")
    if "strategy_type" in _columns("strategy_version"):
        op.drop_column("strategy_version", "strategy_type")
