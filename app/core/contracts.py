"""Stable boundaries shared by later worktrees."""

from dataclasses import dataclass
from datetime import datetime
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
