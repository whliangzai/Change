from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine

from app.core.contracts import AuditEvent, IdempotencyRequest, IdempotencyResult
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.runtime import (
    SqlAlchemyAuditWriter,
    SqlAlchemyIdempotencyStore,
)


def test_idempotency_result_survives_a_new_store_instance(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    request = IdempotencyRequest(
        key="batch-1",
        request_hash="hash-1",
        actor_id="user-1",
        path="/api/v1/data/batches",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    result = IdempotencyResult(201, b'{"data": {}}', "application/json", (("x-test", "1"),))

    first = SqlAlchemyIdempotencyStore.from_engine(engine)
    assert first.acquire(request) is None
    first.complete(request, result)

    replay = SqlAlchemyIdempotencyStore.from_engine(engine).acquire(request)

    assert replay == result


def test_audit_event_is_append_only_and_survives_a_new_writer_instance(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        actor_id=uuid4(),
        actor_roles=("USER",),
        action="DATA_BATCH_CREATE",
        object_type="data_batch",
        object_id="batch-1",
        request_id="req-1",
        result="SUCCESS",
    )

    writer = SqlAlchemyAuditWriter.from_engine(engine)
    writer.append(event)
    writer.append(event)

    assert SqlAlchemyAuditWriter.from_engine(engine).count() == 2
