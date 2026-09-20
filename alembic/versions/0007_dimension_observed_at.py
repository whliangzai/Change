"""Record point-in-time availability for historical dimensions."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0007_dimension_observed_at"
down_revision = "0006_versioned_market_rows"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def _add_observed_at(table: str, value_sql: str) -> None:
    if "observed_at" in _columns(table):
        return
    op.add_column(
        table,
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text(f"UPDATE {table} SET observed_at = {value_sql}"))
    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            "observed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def upgrade() -> None:
    _add_observed_at(
        "security_status_history",
        "COALESCE("
        "(SELECT daily_bar.available_at FROM daily_bar "
        "WHERE daily_bar.security_id = security_status_history.security_id "
        "AND daily_bar.trade_date = security_status_history.effective_date "
        "AND daily_bar.data_batch_id = security_status_history.source_batch_id), "
        "(SELECT data_batch.available_at FROM data_batch "
        "WHERE data_batch.id = security_status_history.source_batch_id)"
        ")",
    )
    _add_observed_at(
        "industry_membership_history",
        "(SELECT data_batch.available_at FROM data_batch "
        "WHERE data_batch.id = industry_membership_history.source_batch_id)",
    )


def downgrade() -> None:
    for table in ("industry_membership_history", "security_status_history"):
        if "observed_at" in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("observed_at")
