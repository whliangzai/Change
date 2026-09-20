"""Shared time-normalization helpers for causal market-data comparisons."""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating SQLite-naive values as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def available_at_or_before(available_at: datetime | None, cutoff: datetime | None) -> bool:
    """Whether a timestamp is proven available by the causal cutoff."""

    if cutoff is None:
        return True
    return available_at is not None and as_utc(available_at) <= as_utc(cutoff)


__all__ = ["as_utc", "available_at_or_before"]
