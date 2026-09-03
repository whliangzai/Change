"""Fail-closed quality gates for authorized market data imports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final


@dataclass(frozen=True, slots=True)
class QualityIssue:
    symbol: str
    trade_date: date | None
    field: str
    message: str


class DataQualityError(ValueError):
    """A blocking quality failure; callers must not publish the batch."""

    blocking: Final[bool] = True

    def __init__(self, issues: Iterable[QualityIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        return "; ".join(
            f"{issue.symbol} {issue.trade_date or 'unknown'} [{issue.field}]: {issue.message}"
            for issue in self.issues
        )


class QualityGate:
    """Validate normalized daily bars without silently changing source values."""

    _required_fields: Final[tuple[str, ...]] = (
        "raw_open", "raw_high", "raw_low", "raw_close", "adjusted_open", "adjusted_high",
        "adjusted_low", "adjusted_close", "volume", "amount", "adjust_factor",
    )

    def validate_daily_bars(self, rows: Iterable[Mapping[str, object]]) -> None:
        issues: list[QualityIssue] = []
        seen: set[tuple[str, date]] = set()
        for row in rows:
            symbol = str(row.get("symbol", ""))
            trade_date = self._as_date(row.get("trade_date"))
            if not symbol:
                issues.append(QualityIssue("<missing>", trade_date, "symbol", "symbol is required"))
                continue
            if trade_date is None:
                issues.append(QualityIssue(symbol, None, "trade_date", "trade_date is required"))
            else:
                key = (symbol, trade_date)
                if key in seen:
                    issues.append(
                        QualityIssue(symbol, trade_date, "security/date", "duplicate security/date")
                    )
                seen.add(key)
            for field in self._required_fields:
                value = row.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    issues.append(QualityIssue(symbol, trade_date, field, "value is required"))
                    continue
                try:
                    parsed = Decimal(str(value))
                    if not parsed.is_finite():
                        issues.append(QualityIssue(symbol, trade_date, field, "value must be finite"))
                    elif parsed < 0:
                        issues.append(QualityIssue(symbol, trade_date, field, "value cannot be negative"))
                except (InvalidOperation, ValueError):
                    issues.append(QualityIssue(symbol, trade_date, field, "value must be numeric"))
        if issues:
            raise DataQualityError(issues)

    def validate_batch_provenance(
        self, *, as_of_date: date, available_at: datetime, information_cutoff_at: datetime
    ) -> None:
        issues: list[QualityIssue] = []
        if available_at.tzinfo is None:
            issues.append(QualityIssue("<batch>", as_of_date, "available_at", "timezone is required"))
        if information_cutoff_at.tzinfo is None:
            issues.append(
                QualityIssue("<batch>", as_of_date, "information_cutoff_at", "timezone is required")
            )
        if information_cutoff_at > available_at:
            issues.append(
                QualityIssue(
                    "<batch>", as_of_date, "information_cutoff_at", "cannot be later than available_at"
                )
            )
        if issues:
            raise DataQualityError(issues)

    def validate_status_history(self, rows: Iterable[Mapping[str, object]], *, as_of_date: date) -> None:
        issues = [
            QualityIssue(
                str(row.get("symbol", "<missing>")),
                self._as_date(row.get("effective_date")),
                "effective_date",
                "future status cannot be used for this as-of date",
            )
            for row in rows
            if (effective := self._as_date(row.get("effective_date"))) is not None
            and effective > as_of_date
        ]
        if issues:
            raise DataQualityError(issues)

    @staticmethod
    def _as_date(value: object) -> date | None:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None
