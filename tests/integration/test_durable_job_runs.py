from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.core.errors import DependencyError
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.runtime import SqlAlchemyJobRunStore
from app.jobs.tasks import import_tushare_data, run_backtest


def test_job_result_replays_after_store_restart(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    calls: list[date] = []

    def service(*, business_date: date) -> dict[str, str]:
        calls.append(business_date)
        return {"status": "completed", "business_date": business_date.isoformat()}

    first = run_backtest(
        date(2026, 9, 3),
        service,
        scope="daily",
        run_store=SqlAlchemyJobRunStore.from_engine(engine),
    )
    second = run_backtest(
        date(2026, 9, 3),
        service,
        scope="daily",
        run_store=SqlAlchemyJobRunStore.from_engine(engine),
    )

    assert first.status == "succeeded"
    assert second.replayed is True
    assert second.run_id == first.run_id
    assert second.value == {"status": "completed", "business_date": "2026-09-03"}
    assert calls == [date(2026, 9, 3)]


def test_dependency_failure_can_retry_using_same_durable_key(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    attempts = 0

    def service(*, business_date: date) -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DependencyError("temporary database outage")
        return {"status": "completed", "business_date": business_date.isoformat()}

    store = SqlAlchemyJobRunStore.from_engine(engine)
    with suppress(DependencyError):
        run_backtest(date(2026, 9, 3), service, scope="retry", run_store=store)

    result = run_backtest(date(2026, 9, 3), service, scope="retry", run_store=store)

    assert result.status == "succeeded"
    assert result.attempt == 2
    assert attempts == 2


def test_provider_retry_keeps_each_attempt_in_job_run_history(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-retry.db'}")
    Base.metadata.create_all(engine)
    attempts = 0

    def service(*, business_date: date, scope: str) -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DependencyError("temporary provider outage")
        return {"business_date": business_date.isoformat(), "scope": scope}

    store = SqlAlchemyJobRunStore.from_engine(engine)
    with suppress(DependencyError):
        import_tushare_data(date(2026, 9, 3), service, scope="pilot", run_store=store)
    result = import_tushare_data(date(2026, 9, 3), service, scope="pilot", run_store=store)

    rows, total = SqlAlchemyJobRunStore.from_engine(engine).page(
        1, 50, provider="tushare", business_date=date(2026, 9, 3)
    )
    assert result.status == "succeeded"
    assert total == 2
    assert [row.status for row in reversed(rows)] == ["failed", "succeeded"]
    assert rows[0].attempt == 2


def test_active_provider_task_key_cannot_have_two_queued_or_running_rows(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'active-task-key.db'}")
    Base.metadata.create_all(engine)
    factory = SqlAlchemyJobRunStore.from_engine(engine)._factory

    first = models.JobRun(
        job_code="data-import",
        business_date=date(2026, 9, 3),
        idempotency_key="data-import:2026-09-03:tushare-pilot",
        status="queued",
        run_number=1,
        attempt=1,
        phase="provider-import",
        started_at=datetime.now(UTC),
    )
    second = models.JobRun(
        job_code="data-import",
        business_date=date(2026, 9, 3),
        idempotency_key=first.idempotency_key,
        status="running",
        run_number=2,
        attempt=2,
        phase="provider-import",
        started_at=datetime.now(UTC),
    )
    with factory() as session:
        session.add(first)
        session.flush()
        session.add(second)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_concurrent_queue_reservations_have_one_enqueue_owner(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-reservation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)

    def reserve(_: int) -> tuple[str, bool]:
        record, created = SqlAlchemyJobRunStore.from_engine(engine).reserve_queued_owned(
            "data-import",
            date(2026, 9, 3),
            "data-import:2026-09-03:tushare-pilot",
            phase="enqueueing",
            value={"provider": "tushare", "scope": "pilot"},
        )
        return record.run_id, created

    with ThreadPoolExecutor(max_workers=4) as pool:
        reservations = list(pool.map(reserve, range(4)))

    assert len({run_id for run_id, _ in reservations}) == 1
    assert sum(created for _, created in reservations) == 1
    assert SqlAlchemyJobRunStore.from_engine(engine).page(1, 50)[1] == 1
