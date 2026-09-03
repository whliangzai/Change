"""Historical, point-in-time stock-pool reconstruction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Final

Row = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class UniverseDecision:
    symbol: str
    eligible: bool
    reason: str | None
    median_amount: Decimal | None = None


class HistoricalUniverseSelector:
    """Apply stock-pool rules using only data available on ``as_of_date``."""

    minimum_listing_days: Final[int] = 60
    minimum_median_amount: Final[Decimal] = Decimal("20000000")

    def select(
        self,
        *,
        as_of_date: date,
        securities: Iterable[Row],
        status_history: Mapping[str, Row] | Iterable[Row],
        daily_bars: Iterable[Row],
        open_trade_dates: Sequence[date],
    ) -> list[UniverseDecision]:
        statuses = self._status_by_symbol(status_history, as_of_date)
        amounts = self._amounts_by_symbol(daily_bars, as_of_date)
        open_dates = sorted(day for day in open_trade_dates if day <= as_of_date)
        decisions: list[UniverseDecision] = []
        for security in securities:
            symbol = self._text(security.get("symbol"))
            listed_on = self._date(security.get("list_date"))
            status = statuses.get(symbol, {})
            reason = (
                "data_not_available_at_as_of"
                if not self._available_on_or_before(security, as_of_date)
                else self._exclusion_reason(security, status, listed_on, open_dates, amounts.get(symbol, []))
            )
            values = sorted(amounts.get(symbol, []))
            median_amount = Decimal(str(median(values))) if values else None
            decisions.append(UniverseDecision(symbol, reason is None, reason, median_amount))
        return decisions

    def eligible_symbols(
        self,
        *,
        as_of_date: date,
        securities: Iterable[Row],
        status_history: Mapping[str, Row] | Iterable[Row],
        daily_bars: Iterable[Row],
        open_trade_dates: Sequence[date],
    ) -> list[str]:
        """Return only eligible symbols while retaining decision logic in one place."""
        decisions = self.select(
            as_of_date=as_of_date,
            securities=securities,
            status_history=status_history,
            daily_bars=daily_bars,
            open_trade_dates=open_trade_dates,
        )
        return [decision.symbol for decision in decisions if decision.eligible]

    def _exclusion_reason(
        self,
        security: Row,
        status: Row,
        listed_on: date | None,
        open_dates: Sequence[date],
        amounts: list[Decimal],
    ) -> str | None:
        if self._text(security.get("security_type")).upper() != "COMMON":
            return "non_common_stock"
        board = self._text(status.get("board") or security.get("board"))
        if board.upper() != "MAIN":
            return "non_mainboard_common_stock"
        if self._truth(status.get("is_st")):
            return "st"
        if self._truth(status.get("is_suspended")):
            return "suspended"
        if self._truth(status.get("is_delist_period")):
            return "delist_period"
        delist_date = self._date(security.get("delist_date"))
        if delist_date is not None and delist_date <= (open_dates[-1] if open_dates else date.min):
            return "delisted"
        if listed_on is None:
            return "missing_list_date"
        listing_days = sum(listed_on <= day for day in open_dates)
        if listing_days < self.minimum_listing_days:
            return "listed_less_than_60_trading_days"
        if len(amounts) < 20:
            return "insufficient_20_day_amount_history"
        amount_median = Decimal(str(median(sorted(amounts))))
        if amount_median < self.minimum_median_amount:
            return "median_amount_below_20000000"
        return None

    @classmethod
    def _status_by_symbol(
        cls, status_history: Mapping[str, Row] | Iterable[Row], as_of_date: date
    ) -> dict[str, Row]:
        if isinstance(status_history, Mapping):
            return {
                symbol: row
                for symbol, row in status_history.items()
                if cls._available_on_or_before(row, as_of_date)
                and ((effective := cls._date(row.get("effective_date"))) is None or effective <= as_of_date)
            }
        selected: dict[str, tuple[date, Row]] = {}
        for row in status_history:
            symbol = cls._text(row.get("symbol"))
            effective = cls._date(row.get("effective_date"))
            if effective is not None and effective <= as_of_date and cls._available_on_or_before(row, as_of_date):
                prior = selected.get(symbol)
                if prior is None or effective > prior[0]:
                    selected[symbol] = (effective, row)
        return {symbol: row for symbol, (_, row) in selected.items()}

    @classmethod
    def _amounts_by_symbol(cls, daily_bars: Iterable[Row], as_of_date: date) -> dict[str, list[Decimal]]:
        by_symbol: dict[str, list[tuple[date, Decimal]]] = {}
        for row in daily_bars:
            symbol = cls._text(row.get("symbol"))
            trade_date = cls._date(row.get("trade_date"))
            if trade_date is None or trade_date > as_of_date or not cls._available_on_or_before(row, as_of_date):
                continue
            try:
                amount = Decimal(str(row.get("amount")))
            except (InvalidOperation, ValueError):
                continue
            by_symbol.setdefault(symbol, []).append((trade_date, amount))
        return {symbol: [amount for _, amount in sorted(values)[-20:]] for symbol, values in by_symbol.items()}

    @staticmethod
    def _text(value: object) -> str:
        return str(value) if value is not None else ""

    @staticmethod
    def _date(value: object) -> date | None:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _truth(value: object) -> bool:
        return value is True or (isinstance(value, str) and value.lower() in {"true", "1", "yes"})

    @classmethod
    def _available_on_or_before(cls, row: Row, as_of_date: date) -> bool:
        value = row.get("available_at")
        if value is None:
            return True
        if isinstance(value, datetime):
            return value.date() <= as_of_date
        if isinstance(value, date):
            return value <= as_of_date
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).date() <= as_of_date
            except ValueError:
                try:
                    return date.fromisoformat(value) <= as_of_date
                except ValueError:
                    return False
        return False
