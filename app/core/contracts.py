"""Stable boundaries shared by later worktrees."""

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuditEvent:
    occurred_at: datetime
    actor_id: UUID | None
    actor_roles: tuple[str, ...]
    action: str
    object_type: str
    object_id: str | None
    request_id: str
    result: str
    idempotency_key: str | None = None
    before_summary: dict[str, Any] | None = None
    after_summary: dict[str, Any] | None = None


class AuditWriter(Protocol):
    """Append-only audit boundary; implementations must never overwrite events."""

    def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditWriter:
    """Development adapter that preserves append order for tests and local use."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class IdempotencyRequest:
    key: str
    request_hash: str
    actor_id: str
    path: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    status_code: int
    body: bytes
    content_type: str | None
    headers: tuple[tuple[str, str], ...]


class IdempotencyStore(Protocol):
    """Reserves a write once and retains its original result for seven days."""

    def acquire(self, request: IdempotencyRequest) -> IdempotencyResult | None: ...

    def complete(self, request: IdempotencyRequest, result: IdempotencyResult) -> None: ...


class InMemoryIdempotencyStore:
    """Process-local atomic adapter replaceable by durable infrastructure storage."""

    def __init__(self) -> None:
        self._requests: dict[str, IdempotencyRequest] = {}
        self._results: dict[str, IdempotencyResult] = {}
        self._lock = Lock()

    def acquire(self, request: IdempotencyRequest) -> IdempotencyResult | None:
        from app.core.errors import IdempotencyConflictError, StateConflictError

        with self._lock:
            existing = self._requests.get(request.key)
            if existing is None or existing.expires_at <= datetime.now(UTC):
                self._requests[request.key] = request
                return None
            if (
                existing.request_hash != request.request_hash
                or existing.actor_id != request.actor_id
                or existing.path != request.path
            ):
                raise IdempotencyConflictError("Idempotency-Key was already used for another request")
            result = self._results.get(request.key)
            if result is None:
                raise StateConflictError("A matching idempotent request is still in progress")
            return result

    def complete(self, request: IdempotencyRequest, result: IdempotencyResult) -> None:
        with self._lock:
            if self._requests.get(request.key) == request:
                self._results[request.key] = result
