"""Add durable request reservations and batch ownership."""

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Uuid, inspect
from sqlalchemy.dialects.sqlite import JSON

from alembic import op

revision = "0002_durable_runtime_storage"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    daily_bar_columns = {column["name"] for column in inspector.get_columns("daily_bar")}
    if "available_at" not in daily_bar_columns:
        op.add_column("daily_bar", Column("available_at", DateTime(timezone=True), nullable=True))
    plan_columns = {column["name"] for column in inspector.get_columns("order_plan")}
    if "version" not in plan_columns:
        op.add_column("order_plan", Column("version", Integer, nullable=False, server_default="1"))
    execution_columns = {column["name"] for column in inspector.get_columns("execution_record")}
    if "idempotency_key" not in execution_columns:
        op.add_column("execution_record", Column("idempotency_key", String(128), nullable=True))
    execution_indexes = {index["name"] for index in inspector.get_indexes("execution_record")}
    if "uq_execution_record_plan_idempotency_key" not in execution_indexes:
        op.create_index(
            "uq_execution_record_plan_idempotency_key",
            "execution_record",
            ["plan_id", "idempotency_key"],
            unique=True,
        )
    batch_columns = {column["name"] for column in inspector.get_columns("data_batch")}
    if "owner_id" not in batch_columns:
        op.add_column("data_batch", Column("owner_id", String(36), nullable=True))
    job_columns = {column["name"] for column in inspector.get_columns("job_run")}
    if "run_number" not in job_columns:
        op.add_column("job_run", Column("run_number", Integer, nullable=True))
    if "phase" not in job_columns:
        op.add_column("job_run", Column("phase", String(32), nullable=True))
    if "value" not in job_columns:
        op.add_column("job_run", Column("value", JSON, nullable=True))
    run_columns = {column["name"] for column in inspector.get_columns("backtest_run")}
    if "result_snapshot_hash" not in run_columns:
        op.add_column("backtest_run", Column("result_snapshot_hash", String(64), nullable=True))
    if "result_summary" not in run_columns:
        op.add_column("backtest_run", Column("result_summary", JSON, nullable=True))
    if "report_export" not in inspector.get_table_names():
        op.create_table(
            "report_export",
            Column("id", Uuid(as_uuid=True), primary_key=True),
            Column("report_id", String(64), nullable=False),
            Column("owner_id", Uuid(as_uuid=True), nullable=False),
            Column("status", String(16), nullable=False),
            Column("file_path", String(), nullable=False),
            Column("content_hash", String(64), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=False),
        )
    if "idempotency_record" not in inspector.get_table_names():
        op.create_table(
            "idempotency_record",
            Column("key", String(128), primary_key=True),
            Column("request_hash", String(64), nullable=False),
            Column("actor_id", String(64), nullable=False),
            Column("path", String(256), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=False),
            Column("status_code", Integer, nullable=True),
            Column("body", LargeBinary, nullable=True),
            Column("content_type", String(128), nullable=True),
            Column("headers", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
    if "auth_session" not in inspector.get_table_names():
        op.create_table(
            "auth_session",
            Column("session_id", Uuid(as_uuid=True), primary_key=True),
            Column("user_id", Uuid(as_uuid=True), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("revoked_at", DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "idempotency_record" in inspector.get_table_names():
        op.drop_table("idempotency_record")
    if "auth_session" in inspector.get_table_names():
        op.drop_table("auth_session")
    batch_columns = {column["name"] for column in inspector.get_columns("data_batch")}
    if "owner_id" in batch_columns:
        op.drop_column("data_batch", "owner_id")
    job_columns = {column["name"] for column in inspector.get_columns("job_run")}
    for column in ("value", "phase", "run_number"):
        if column in job_columns:
            op.drop_column("job_run", column)
    run_columns = {column["name"] for column in inspector.get_columns("backtest_run")}
    for column in ("result_summary", "result_snapshot_hash"):
        if column in run_columns:
            op.drop_column("backtest_run", column)
    if "report_export" in inspector.get_table_names():
        op.drop_table("report_export")
    daily_bar_columns = {column["name"] for column in inspector.get_columns("daily_bar")}
    if "available_at" in daily_bar_columns:
        op.drop_column("daily_bar", "available_at")
    plan_columns = {column["name"] for column in inspector.get_columns("order_plan")}
    if "version" in plan_columns:
        op.drop_column("order_plan", "version")
    execution_indexes = {index["name"] for index in inspector.get_indexes("execution_record")}
    if "uq_execution_record_plan_idempotency_key" in execution_indexes:
        op.drop_index("uq_execution_record_plan_idempotency_key", table_name="execution_record")
    execution_columns = {column["name"] for column in inspector.get_columns("execution_record")}
    if "idempotency_key" in execution_columns:
        op.drop_column("execution_record", "idempotency_key")
