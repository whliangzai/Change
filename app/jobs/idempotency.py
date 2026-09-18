"""Append-only job run tracking and deterministic idempotency."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.core.contracts import AuditEvent, AuditWriter, InMemoryAuditWriter
from app.core.errors import DependencyError, StateConflictError

JobKind = Literal[
    "calendar",
    "data-import",
    "data-quality",
    "daily-report",
    "backtest",
    "backup",
]


@dataclass(frozen=True, slots=True)
class JobResult:
    """Public outcome returned by every job boundary."""

    status: Literal["succeeded", "failed"]
    idempotency_key: str
    business_date: date
    run_id: str
    run_number: int
    attempt: int
    phase: str
    value: Any = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class JobRunRecord:
    """One immutable snapshot of a job attempt."""

    run_id: str
    idempotency_key: str
    job_kind: str
    business_date: date
    run_number: int
    attempt: int
    phase: str
    status: Literal["queued", "running", "succeeded", "failed"]
    error_summary: str | None = None
    value: Any = None
    started_at: datetime = datetime.min.replace(tzinfo=UTC)
    finished_at: datetime | None = None


class JobRunStore(Protocol):
    def latest(self, idempotency_key: str) -> JobRunRecord | None: ...

    def get(self, run_id: str) -> JobRunRecord | None: ...

    def page(
        self,
        page: int,
        page_size: int,
        *,
        provider: str | None = None,
        status: str | None = None,
        business_date: date | None = None,
    ) -> tuple[list[JobRunRecord], int]: ...

    def reserve_queued(
        self,
        job_kind: str,
        business_date: date,
        idempotency_key: str,
        *,
        phase: str,
        value: Any = None,
        retry_failed: bool = False,
    ) -> JobRunRecord: ...

    def reserve_queued_owned(
        self,
        job_kind: str,
        business_date: date,
        idempotency_key: str,
        *,
        phase: str,
        value: Any = None,
        retry_failed: bool = False,
    ) -> tuple[JobRunRecord, bool]: ...

    def claim_queued(self, idempotency_key: str, *, phase: str) -> JobRunRecord | None: ...

    def claim_queued_for_requeue(
        self, run_id: str, *, expected_started_at: datetime
    ) -> tuple[JobRunRecord, bool]: ...

    def mark_queued(self, run_id: str, *, phase: str) -> JobRunRecord | None: ...

    def append(self, record: JobRunRecord) -> None: ...

    def replace(self, record: JobRunRecord) -> None: ...


class InMemoryJobRunStore:
    """Thread-safe adapter used by local/test runs; production can inject a DB adapter."""

    def __init__(self) -> None:
        self.records: list[JobRunRecord] = []
        self._lock = Lock()

    def latest(self, idempotency_key: str) -> JobRunRecord | None:
        with self._lock:
            for record in reversed(self.records):
                if record.idempotency_key == idempotency_key:
                    return record
        return None

    def get(self, run_id: str) -> JobRunRecord | None:
        with self._lock:
            return next(
                (record for record in reversed(self.records) if record.run_id == run_id), None
            )

    def page(
        self,
        page: int,
        page_size: int,
        *,
        provider: str | None = None,
        status: str | None = None,
        business_date: date | None = None,
    ) -> tuple[list[JobRunRecord], int]:
        normalized_status = status.lower() if status else None
        normalized_provider = provider.lower() if provider else None
        with self._lock:
            records = [
                record
                for record in reversed(self.records)
                if (normalized_status is None or record.status == normalized_status)
                and (business_date is None or record.business_date == business_date)
                and (
                    normalized_provider is None
                    or provider_from_task_key(record.idempotency_key) == normalized_provider
                )
            ]
            total = len(records)
            start = (page - 1) * page_size
            return records[start : start + page_size], total

    def reserve_queued(
        self,
        job_kind: str,
        business_date: date,
        idempotency_key: str,
        *,
        phase: str,
        value: Any = None,
        retry_failed: bool = False,
    ) -> JobRunRecord:
        return self.reserve_queued_owned(
            job_kind,
            business_date,
            idempotency_key,
            phase=phase,
            value=value,
            retry_failed=retry_failed,
        )[0]

    def reserve_queued_owned(
        self,
        job_kind: str,
        business_date: date,
        idempotency_key: str,
        *,
        phase: str,
        value: Any = None,
        retry_failed: bool = False,
    ) -> tuple[JobRunRecord, bool]:
        with self._lock:
            previous = next(
                (
                    record
                    for record in reversed(self.records)
                    if record.idempotency_key == idempotency_key
                ),
                None,
            )
            if previous is not None and (not retry_failed or previous.status != "failed"):
                return previous, False
            if previous is not None and previous.status != "failed":
                raise StateConflictError("Only failed jobs can be queued for retry")
            record = JobRunRecord(
                run_id=str(uuid4()),
                idempotency_key=idempotency_key,
                job_kind=job_kind,
                business_date=business_date,
                run_number=(previous.run_number + 1) if previous else 1,
                attempt=(previous.attempt + 1) if previous else 1,
                phase=phase,
                status="queued",
                value=value,
                started_at=datetime.now(UTC),
            )
            self.records.append(record)
            return record, True

    def claim_queued(self, idempotency_key: str, *, phase: str) -> JobRunRecord | None:
        with self._lock:
            previous = next(
                (
                    record
                    for record in reversed(self.records)
                    if record.idempotency_key == idempotency_key
                ),
                None,
            )
            if previous is None:
                return None
            if previous.status == "running":
                raise StateConflictError("A matching job is already running")
            if previous.status != "queued":
                return None
            claimed = replace(
                previous,
                status="running",
                phase=phase,
                started_at=datetime.now(UTC),
                error_summary=None,
                finished_at=None,
            )
            self.records[self.records.index(previous)] = claimed
            return claimed

    def claim_queued_for_requeue(
        self, run_id: str, *, expected_started_at: datetime
    ) -> tuple[JobRunRecord, bool]:
        with self._lock:
            previous = next(
                (record for record in reversed(self.records) if record.run_id == run_id), None
            )
            if previous is None or previous.status != "queued":
                if previous is None:
                    raise StateConflictError("The requested queued job no longer exists")
                return previous, False
            if previous.started_at != expected_started_at:
                return previous, False
            claimed = replace(
                previous,
                phase="requeueing",
                started_at=datetime.now(UTC),
                error_summary=None,
                finished_at=None,
            )
            self.records[self.records.index(previous)] = claimed
            return claimed, True

    def mark_queued(self, run_id: str, *, phase: str) -> JobRunRecord | None:
        with self._lock:
            for index in range(len(self.records) - 1, -1, -1):
                record = self.records[index]
                if record.run_id != run_id:
                    continue
                if record.status == "queued":
                    record = replace(record, phase=phase)
                    self.records[index] = record
                return record
        return None

    def append(self, record: JobRunRecord) -> None:
        with self._lock:
            self.records.append(record)

    def replace(self, record: JobRunRecord) -> None:
        with self._lock:
            for index in range(len(self.records) - 1, -1, -1):
                if self.records[index].run_id == record.run_id:
                    self.records[index] = record
                    return
            raise KeyError(record.run_id)


def make_idempotency_key(job_kind: str, business_date: date, scope: str | None = None) -> str:
    """Build a stable key; scope is used for distinct backtests or data batches."""
    base = f"{job_kind}:{business_date.isoformat()}"
    return f"{base}:{scope}" if scope else base


def provider_from_task_key(idempotency_key: str) -> str | None:
    """Extract the non-sensitive provider namespace from a provider task key."""
    parts = idempotency_key.split(":")
    if len(parts) != 3 or parts[0] != "data-import":
        return None
    provider_scope = parts[2].rsplit("-", 1)
    if len(provider_scope) != 2 or provider_scope[0] not in {"ifind", "tushare"}:
        return None
    return provider_scope[0]


def scope_from_task_key(idempotency_key: str) -> str | None:
    parts = idempotency_key.split(":")
    if len(parts) != 3 or parts[0] != "data-import":
        return None
    provider_scope = parts[2].rsplit("-", 1)
    return (
        provider_scope[1]
        if len(provider_scope) == 2 and provider_scope[1] in {"pilot", "full"}
        else None
    )


def retryable_error_summary(error_summary: str | None) -> bool:
    """Classify a persisted error without attempting to parse provider payloads."""
    if not error_summary:
        return False
    return error_summary.startswith(("DependencyError:", "ConnectionError:", "TimeoutError:"))


def is_retryable(exc: BaseException) -> bool:
    """Only infrastructure/dependency failures may be retried automatically."""
    return isinstance(exc, (DependencyError, ConnectionError, TimeoutError))


def _error_summary(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {message[:240]}"


class IdempotentTask:
    """Callable task runner with one side-effect execution per stable job key."""

    def __call__(
        self,
        job_kind: str,
        business_date: date,
        operation: Callable[[], Any],
        *,
        run_store: JobRunStore | None = None,
        audit_writer: AuditWriter | None = None,
        scope: str | None = None,
        phase: str = "execute",
    ) -> JobResult:
        store = run_store or _DEFAULT_RUN_STORE
        audit = audit_writer or _DEFAULT_AUDIT_WRITER
        key = make_idempotency_key(job_kind, business_date, scope)
        previous = store.latest(key)
        if previous is not None and previous.status == "succeeded":
            return JobResult(
                status="succeeded",
                idempotency_key=key,
                business_date=business_date,
                run_id=previous.run_id,
                run_number=previous.run_number,
                attempt=previous.attempt,
                phase=previous.phase,
                value=previous.value,
                replayed=True,
            )
        if previous is not None and previous.status == "running":
            raise StateConflictError("A matching job is already running")

        if previous is not None and previous.status == "queued":
            claimed = store.claim_queued(key, phase=phase)
            if claimed is None:
                raise StateConflictError("A matching queued job could not be claimed")
            record = claimed
        else:
            run_number = (previous.run_number + 1) if previous else 1
            attempt = (previous.attempt + 1) if previous else 1
            record = JobRunRecord(
                run_id=str(uuid4()),
                idempotency_key=key,
                job_kind=job_kind,
                business_date=business_date,
                run_number=run_number,
                attempt=attempt,
                phase=phase,
                status="running",
                started_at=datetime.now(UTC),
            )
            store.append(record)
        try:
            value = operation()
        except BaseException as exc:
            failed = replace(
                record,
                status="failed",
                error_summary=_error_summary(exc),
                finished_at=datetime.now(UTC),
            )
            store.replace(failed)
            audit.append(
                AuditEvent(
                    occurred_at=failed.finished_at or datetime.now(UTC),
                    actor_id=None,
                    actor_roles=("SYSTEM",),
                    action=f"job.{job_kind}",
                    object_type="job_run",
                    object_id=failed.run_id,
                    request_id=failed.run_id,
                    result="failure",
                    idempotency_key=key,
                    after_summary={"status": "failed", "error": failed.error_summary},
                )
            )
            raise

        succeeded = replace(
            record,
            status="succeeded",
            value=value,
            finished_at=datetime.now(UTC),
        )
        store.replace(succeeded)
        audit.append(
            AuditEvent(
                occurred_at=succeeded.finished_at or datetime.now(UTC),
                actor_id=None,
                actor_roles=("SYSTEM",),
                action=f"job.{job_kind}",
                object_type="job_run",
                object_id=succeeded.run_id,
                request_id=succeeded.run_id,
                result="success",
                idempotency_key=key,
                after_summary={"status": "succeeded", "attempt": record.attempt},
            )
        )
        return JobResult(
            status="succeeded",
            idempotency_key=key,
            business_date=business_date,
            run_id=succeeded.run_id,
            run_number=record.run_number,
            attempt=record.attempt,
            phase=phase,
            value=value,
        )

    @staticmethod
    def retryable(exc: BaseException) -> bool:
        return is_retryable(exc)


_DEFAULT_RUN_STORE = InMemoryJobRunStore()
_DEFAULT_AUDIT_WRITER = InMemoryAuditWriter()
run_idempotent_task = IdempotentTask()


__all__ = [
    "IdempotentTask",
    "InMemoryJobRunStore",
    "JobResult",
    "JobRunRecord",
    "JobRunStore",
    "is_retryable",
    "make_idempotency_key",
    "provider_from_task_key",
    "retryable_error_summary",
    "run_idempotent_task",
    "scope_from_task_key",
]
