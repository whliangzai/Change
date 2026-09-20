"""Feature value objects and causal rolling feature calculations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.domain.causality import available_at_or_before


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    symbol: str
    trade_date: date
    close: Decimal | None
    ma10: Decimal | None
    market_close: Decimal | None
    market_ma20: Decimal | None
    return_5d: Decimal | None
    return_20d: Decimal | None
    amount_ratio: Decimal | None
    reliably_buyable: bool
    available_at: datetime | None = None
    industry: str | None = None
    is_st: bool = False
    is_suspended: bool = False
    is_delisted: bool = False
    is_limit_up: bool = False


@dataclass(frozen=True, slots=True)
class DailyBar:
    symbol: str
    trade_date: date
    close: Decimal
    amount: Decimal
    available_at: datetime | None = None
    is_suspended: bool = False
    is_st: bool = False
    is_delisted: bool = False
    is_limit_up: bool = False
    industry: str | None = None
    list_date: date | None = None


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def build_feature_snapshot(
    bars: Sequence[DailyBar],
    market_bars: Sequence[DailyBar],
    *,
    symbol: str,
    trade_date: date,
    information_cutoff_at: datetime | None = None,
) -> FeatureSnapshot:
    """Build a snapshot using only bars through ``trade_date``.

    The function deliberately returns missing features instead of inventing values when
    history is too short. Callers can therefore make data availability explicit.
    """

    def available(bar: DailyBar) -> bool:
        return available_at_or_before(bar.available_at, information_cutoff_at)

    stock = sorted(
        (
            bar
            for bar in bars
            if bar.symbol == symbol and bar.trade_date <= trade_date and available(bar)
        ),
        key=lambda b: b.trade_date,
    )
    market = sorted(
        (bar for bar in market_bars if bar.trade_date <= trade_date and available(bar)),
        key=lambda b: b.trade_date,
    )
    current = next((bar for bar in reversed(stock) if bar.trade_date == trade_date), None)
    if current is None:
        return FeatureSnapshot(symbol, trade_date, None, None, None, None, None, None, None, False)
    closes = [bar.close for bar in stock]
    amounts = [bar.amount for bar in stock]
    market_closes = [bar.close for bar in market]
    ma10 = _mean(closes[-10:]) if len(closes) >= 10 else None
    market_ma20 = _mean(market_closes[-20:]) if len(market_closes) >= 20 else None
    return_5d = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else None
    return_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else None
    avg5 = _mean(amounts[-5:]) if len(amounts) >= 5 else None
    avg20 = _mean(amounts[-20:]) if len(amounts) >= 20 else None
    ratio = (
        avg5 / avg20 if avg5 is not None and avg20 is not None and avg20 != Decimal("0") else None
    )
    market_current = next((bar for bar in reversed(market) if bar.trade_date == trade_date), None)
    return FeatureSnapshot(
        symbol=symbol,
        trade_date=trade_date,
        close=current.close,
        ma10=ma10,
        market_close=market_current.close if market_current else None,
        market_ma20=market_ma20,
        return_5d=return_5d,
        return_20d=return_20d,
        amount_ratio=ratio,
        reliably_buyable=not current.is_suspended and not current.is_limit_up,
        available_at=current.available_at,
        industry=current.industry,
        is_st=current.is_st,
        is_suspended=current.is_suspended,
        is_delisted=current.is_delisted,
        is_limit_up=current.is_limit_up,
    )


compute_features = build_feature_snapshot
