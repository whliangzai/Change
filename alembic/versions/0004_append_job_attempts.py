"""Allow durable job-run history for retries with one row per attempt."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0004_append_job_attempts"
down_revision = "0003_durable_market_import"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_job_run_job_idempotency"
_INDEX = "uq_job_run_active_task_key"
_ACTIVE_STATUS = text("status IN ('queued', 'running')")


def upgrade() -> None:
    bind = op.get_bind()
    constraints = {item["name"] for item in inspect(bind).get_unique_constraints("job_run")}
    if _CONSTRAINT in constraints and bind.dialect.name == "sqlite":
        with op.batch_alter_table("job_run") as batch:
            batch.drop_constraint(_CONSTRAINT, type_="unique")
    elif _CONSTRAINT in constraints:
        op.drop_constraint(_CONSTRAINT, "job_run", type_="unique")
    indexes = {item["name"] for item in inspect(bind).get_indexes("job_run")}
    if _INDEX not in indexes:
        op.create_index(
            _INDEX,
            "job_run",
            ["idempotency_key"],
            unique=True,
            sqlite_where=_ACTIVE_STATUS,
            postgresql_where=_ACTIVE_STATUS,
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in inspect(bind).get_indexes("job_run")}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name="job_run")
    constraints = {item["name"] for item in inspect(bind).get_unique_constraints("job_run")}
    if _CONSTRAINT in constraints:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("job_run") as batch:
            batch.create_unique_constraint(_CONSTRAINT, ["job_code", "idempotency_key"])
    else:
        op.create_unique_constraint(_CONSTRAINT, "job_run", ["job_code", "idempotency_key"])
