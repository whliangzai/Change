from threading import Event, get_ident

import pytest

from app.core.errors import DependencyError
from app.jobs.provider_execution import BackgroundProviderDispatcher


def test_background_dispatcher_returns_immediately_and_runs_serially() -> None:
    dispatcher = BackgroundProviderDispatcher()
    dispatcher.start()
    release = Event()
    first_started = Event()
    order: list[tuple[str, int]] = []
    request_thread = get_ident()

    def first() -> None:
        order.append(("first", get_ident()))
        first_started.set()
        release.wait(2)

    def second() -> None:
        order.append(("second", get_ident()))

    first_job = dispatcher.enqueue(first, job_id="job-1")
    second_job = dispatcher.enqueue(second, job_id="job-2")
    assert first_job.id == "job-1"
    assert second_job.id == "job-2"
    assert first_started.wait(1)
    assert order == [("first", order[0][1])]
    assert order[0][1] != request_thread

    release.set()
    dispatcher.shutdown()
    assert [name for name, _ in order] == ["first", "second"]
    assert order[0][1] == order[1][1]


def test_background_dispatcher_shutdown_waits_and_rejects_new_work() -> None:
    dispatcher = BackgroundProviderDispatcher()
    dispatcher.start()
    completed = Event()
    dispatcher.enqueue(completed.set, job_id="accepted")
    dispatcher.shutdown()

    assert completed.is_set()
    with pytest.raises(DependencyError, match="unavailable"):
        dispatcher.enqueue(lambda: None, job_id="rejected")


def test_background_dispatcher_logs_only_job_and_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = BackgroundProviderDispatcher()
    dispatcher.start()
    logged: list[tuple[object, ...]] = []

    class RecordingLogger:
        def error(self, *args: object, **_kwargs: object) -> None:
            logged.append(args)

    monkeypatch.setattr("app.jobs.provider_execution.logger", RecordingLogger())

    def fail() -> None:
        raise RuntimeError("token=super-secret raw-provider-response")

    job = dispatcher.enqueue(fail, job_id="safe-job-id")
    with pytest.raises(RuntimeError):
        job.future.result(timeout=1)
    dispatcher.shutdown()

    log_text = " ".join(str(value) for args in logged for value in args)
    assert "safe-job-id" in log_text
    assert "RuntimeError" in log_text
    assert "super-secret" not in log_text
    assert "raw-provider-response" not in log_text


def test_background_dispatcher_reports_callable_failure_to_persistence_boundary() -> None:
    failures: list[tuple[str, str]] = []
    dispatcher = BackgroundProviderDispatcher(
        on_failure=lambda job_id, error: failures.append((job_id, type(error).__name__))
    )
    dispatcher.start()

    def fail_before_task_claim() -> None:
        raise RuntimeError("provider entry point failed")

    job = dispatcher.enqueue(fail_before_task_claim, job_id="persist-me")
    with pytest.raises(RuntimeError):
        job.future.result(timeout=1)
    dispatcher.shutdown()

    assert failures == [("persist-me", "RuntimeError")]
