from datetime import date

import pytest

from app.core.errors import DataUnavailableError, DependencyError
from app.jobs.idempotency import InMemoryJobRunStore
from app.jobs.tasks import run_idempotent_task


def test_dependency_failure_is_retryable_but_data_error_is_not() -> None:
    assert run_idempotent_task.retryable(DependencyError("redis down")) is True
    assert run_idempotent_task.retryable(DataUnavailableError("bad prices")) is False


def test_repeated_job_key_executes_once_and_replays_result() -> None:
    store = InMemoryJobRunStore()
    calls: list[str] = []

    def work() -> dict[str, str]:
        calls.append("called")
        return {"status": "ok"}

    first = run_idempotent_task("daily-report", date(2026, 9, 3), work, run_store=store)
    second = run_idempotent_task("daily-report", date(2026, 9, 3), work, run_store=store)

    assert first.value == second.value
    assert first.run_id == second.run_id
    assert first.status == "succeeded"
    assert first.replayed is False
    assert second.replayed is True
    assert calls == ["called"]
    assert len(store.records) == 1


def test_failed_job_keeps_run_log_and_can_be_retried() -> None:
    store = InMemoryJobRunStore()
    attempts = iter([DependencyError("database unavailable"), {"status": "ok"}])

    def work() -> dict[str, str]:
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    with pytest.raises(DependencyError):
        run_idempotent_task("backtest", date(2026, 9, 3), work, run_store=store)

    recovered = run_idempotent_task("backtest", date(2026, 9, 3), work, run_store=store)
    assert recovered.status == "succeeded"
    assert recovered.attempt == 2
    assert len(store.records) == 2
