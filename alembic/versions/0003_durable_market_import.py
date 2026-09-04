"""Persist import provenance and time-range metadata for market batches."""

from sqlalchemy import Column, Date, Integer, String, inspect

from alembic import op

revision = "0003_durable_market_import"
down_revision = "0002_durable_runtime_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("data_batch")}
    additions = (
        ("file_hash", Column("file_hash", String(64), nullable=True)),
        ("start_date", Column("start_date", Date(), nullable=True)),
        ("end_date", Column("end_date", Date(), nullable=True)),
        ("record_count", Column("record_count", Integer(), nullable=False, server_default="0")),
        ("file_location", Column("file_location", String(1024), nullable=True)),
        ("license_note", Column("license_note", String(2048), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("data_batch", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("data_batch")}
    for name in (
        "license_note",
        "file_location",
        "record_count",
        "end_date",
        "start_date",
        "file_hash",
    ):
        if name in columns:
            op.drop_column("data_batch", name)
