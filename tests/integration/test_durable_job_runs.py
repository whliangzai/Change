from contextlib import suppress
from datetime import date

from sqlalchemy import create_engine

from app.core.errors import DependencyError
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.runtime import SqlAlchemyJobRunStore
from app.jobs.tasks import run_backtest


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
