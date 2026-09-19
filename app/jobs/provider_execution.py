"""Process-local provider import execution for development and tests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any

from app.core.errors import DependencyError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    id: str
    future: Future[Any]


class BackgroundProviderDispatcher:
    """Single-threaded, lifecycle-bound provider task dispatcher."""

    def __init__(
        self,
        *,
        on_failure: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._accepting = False
        self._on_failure = on_failure

    @property
    def available(self) -> bool:
        with self._lock:
            return self._accepting and self._executor is not None

    def start(self) -> None:
        with self._lock:
            if self._executor is not None:
                return
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-import")
            self._accepting = True

    def enqueue(
        self,
        function: Callable[..., Any],
        *args: Any,
        job_id: str,
        **kwargs: Any,
    ) -> BackgroundJob:
        with self._lock:
            if not self._accepting or self._executor is None:
                raise DependencyError("Background provider execution is unavailable")
            future = self._executor.submit(function, *args, **kwargs)
        future.add_done_callback(lambda completed: self._handle_completion(job_id, completed))
        return BackgroundJob(job_id, future)

    def shutdown(self) -> None:
        with self._lock:
            self._accepting = False
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    def _handle_completion(self, job_id: str, future: Future[Any]) -> None:
        try:
            error = future.exception()
        except BaseException as exc:  # pragma: no cover - defensive Future boundary
            error = exc
        if error is not None:
            if self._on_failure is not None:
                try:
                    self._on_failure(job_id, error)
                except BaseException as callback_error:  # pragma: no cover - defensive boundary
                    logger.error(
                        "Background provider failure persistence failed "
                        "job_id=%s exception_type=%s",
                        job_id,
                        type(callback_error).__name__,
                        extra={
                            "job_id": job_id,
                            "event_code": "provider_import_failure_persistence_failed",
                            "exception_type": type(callback_error).__name__,
                        },
                    )
            logger.error(
                "Background provider import failed job_id=%s exception_type=%s",
                job_id,
                type(error).__name__,
                extra={
                    "job_id": job_id,
                    "event_code": "provider_import_background_failed",
                    "exception_type": type(error).__name__,
                },
            )


__all__ = ["BackgroundJob", "BackgroundProviderDispatcher"]
