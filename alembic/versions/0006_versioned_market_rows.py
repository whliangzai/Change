"""Version market rows by batch and split open/close price-limit evidence."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op
from app.infrastructure.db.base import NAMING_CONVENTION

revision = "0006_versioned_market_rows"
down_revision = "0005_multi_strategy_engine_v2"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def _primary_key_columns(table: str) -> list[str]:
    return list(inspect(op.get_bind()).get_pk_constraint(table)["constrained_columns"])


def _replace_primary_key(table: str, columns: list[str]) -> None:
    if _primary_key_columns(table) == columns:
        return
    with op.batch_alter_table(
        table,
        recreate="always",
        naming_convention=NAMING_CONVENTION,
    ) as batch:
        batch.drop_constraint(f"pk_{table}", type_="primary")
        batch.create_primary_key(f"pk_{table}", columns)


def upgrade() -> None:
    existing = _columns("daily_bar")
    added_explicit_limits = False
    for name in (
        "open_limit_up",
        "open_limit_down",
        "close_limit_up",
        "close_limit_down",
    ):
        if name not in existing:
            op.add_column("daily_bar", sa.Column(name, sa.Boolean(), nullable=True))
            added_explicit_limits = True
    if added_explicit_limits:
        op.execute(
            sa.text(
                "UPDATE daily_bar SET open_limit_up = limit_up, "
                "open_limit_down = limit_down, close_limit_up = NULL, close_limit_down = NULL"
            )
        )

    _replace_primary_key("daily_bar", ["security_id", "trade_date", "data_batch_id"])
    _replace_primary_key(
        "adjustment_factor",
        ["security_id", "effective_date", "factor_type", "data_batch_id"],
    )
    _replace_primary_key(
        "security_status_history",
        ["security_id", "effective_date", "source_batch_id"],
    )
    _replace_primary_key(
        "industry_membership_history",
        ["security_id", "industry_code", "effective_from", "source_batch_id"],
    )


def downgrade() -> None:
    _replace_primary_key("daily_bar", ["security_id", "trade_date"])
    _replace_primary_key("adjustment_factor", ["security_id", "effective_date", "factor_type"])
    _replace_primary_key("security_status_history", ["security_id", "effective_date"])
    _replace_primary_key(
        "industry_membership_history",
        ["security_id", "industry_code", "effective_from"],
    )
    existing = _columns("daily_bar")
    with op.batch_alter_table("daily_bar") as batch:
        for name in (
            "close_limit_down",
            "close_limit_up",
            "open_limit_down",
            "open_limit_up",
        ):
            if name in existing:
                batch.drop_column(name)
