"""Trading-day calendar calculations with explicit previous/next links."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class CalendarDay:
    exchange: str
    trade_date: date
    is_open: bool
    prev_trade_date: date | None
    next_trade_date: date | None


class TradeCalendar:
    def build(
        self,
        exchange: str,
        dates: Iterable[date],
        *,
        open_by_date: Mapping[date, bool] | None = None,
    ) -> list[CalendarDay]:
        supplied_dates = list(dates)
        if len(set(supplied_dates)) != len(supplied_dates):
            raise ValueError("duplicate trade calendar dates are not allowed")
        sorted_dates = sorted(supplied_dates)
        flags = open_by_date or {}
        open_dates = [day for day in sorted_dates if flags.get(day, True)]
        result: list[CalendarDay] = []
        for day in sorted_dates:
            previous = next((candidate for candidate in reversed(open_dates) if candidate < day), None)
            following = next((candidate for candidate in open_dates if candidate > day), None)
            result.append(CalendarDay(exchange, day, flags.get(day, True), previous, following))
        return result

    @staticmethod
    def trading_days_between(days: Iterable[CalendarDay], start: date, end: date) -> list[date]:
        return [day.trade_date for day in days if day.is_open and start <= day.trade_date <= end]
