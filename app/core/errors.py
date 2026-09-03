"""Domain errors that are safe to expose through the API boundary."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ApplicationError(Exception):
    code: str
    message: str
    status_code: int
    details: list[dict[str, Any]] = field(default_factory=list)


class ValidationError(ApplicationError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message, 400, details or [])


class DataUnavailableError(ApplicationError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__("BT_DATA_UNAVAILABLE", message, 422, details or [])


class RuleViolationError(ApplicationError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__("RULE_VIOLATION", message, 422, details or [])


class StateConflictError(ApplicationError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__("STATE_CONFLICT", message, 409, details or [])


class DependencyError(ApplicationError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__("DEPENDENCY_UNAVAILABLE", message, 503, details or [])
