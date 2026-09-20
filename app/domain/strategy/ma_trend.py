"""Deterministic MA20/MA60 trend strategy primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.domain.causality import available_at_or_before
from app.domain.strategy.features import DailyBar
from app.domain.strategy.strong_trend import EntrySignal, ExitSignal


@dataclass(frozen=True, slots=True)
class MATrendConfig:
    short_window: int = 20
    long_window: int = 60
    max_positions: int = 4
    max_per_industry: int = 2
    max_holding_days: int = 20
    require_market_above_long_ma: bool = True


@dataclass(frozen=True, slots=True)
class MATrendFeature:
    symbol: str
    trade_date: date
    close: Decimal | None
    short_ma: Decimal | None
    long_ma: Decimal | None
    market_close: Decimal | None
    market_long_ma: Decimal | None
    available_at: datetime | None = None
    industry: str | None = None
    is_st: bool = False
    is_suspended: bool = False
    is_delisted: bool = False
    is_limit_up: bool = False


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def build_ma_feature(
    bars: Sequence[DailyBar],
    market_bars: Sequence[DailyBar],
    *,
    symbol: str,
    trade_date: date,
    short_window: int,
    long_window: int,
    information_cutoff_at: datetime | None = None,
) -> MATrendFeature:
    """Build an MA feature using only observations causally available on ``trade_date``."""

    def visible(bar: DailyBar) -> bool:
        return available_at_or_before(bar.available_at, information_cutoff_at)

    stock = sorted(
        (
            bar
            for bar in bars
            if bar.symbol == symbol and bar.trade_date <= trade_date and visible(bar)
        ),
        key=lambda item: item.trade_date,
    )
    market = sorted(
        (bar for bar in market_bars if bar.trade_date <= trade_date and visible(bar)),
        key=lambda item: item.trade_date,
    )
    current = next((bar for bar in reversed(stock) if bar.trade_date == trade_date), None)
    market_current = next((bar for bar in reversed(market) if bar.trade_date == trade_date), None)
    if current is None:
        return MATrendFeature(symbol, trade_date, None, None, None, None, None)
    closes = [bar.close for bar in stock]
    market_closes = [bar.close for bar in market]
    return MATrendFeature(
        symbol=symbol,
        trade_date=trade_date,
        close=current.close,
        short_ma=_mean(closes[-short_window:]) if len(closes) >= short_window else None,
        long_ma=_mean(closes[-long_window:]) if len(closes) >= long_window else None,
        market_close=market_current.close if market_current else None,
        market_long_ma=(
            _mean(market_closes[-long_window:]) if len(market_closes) >= long_window else None
        ),
        available_at=current.available_at,
        industry=current.industry,
        is_st=current.is_st,
        is_suspended=current.is_suspended,
        is_delisted=current.is_delisted,
        is_limit_up=current.is_limit_up,
    )


class MATrendStrategy:
    def __init__(self, config: MATrendConfig | None = None) -> None:
        self.config = config or MATrendConfig()

    def is_candidate(
        self, feature: MATrendFeature, *, information_cutoff_at: datetime | None = None
    ) -> bool:
        if not available_at_or_before(feature.available_at, information_cutoff_at):
            return False
        required = (
            feature.close,
            feature.short_ma,
            feature.long_ma,
            feature.market_close,
            feature.market_long_ma,
        )
        if any(value is None for value in required):
            return False
        assert feature.close is not None
        assert feature.short_ma is not None
        assert feature.long_ma is not None
        assert feature.market_close is not None
        assert feature.market_long_ma is not None
        market_open = (
            not self.config.require_market_above_long_ma
            or feature.market_close > feature.market_long_ma
        )
        return (
            market_open
            and feature.close > feature.short_ma > feature.long_ma
            and not feature.is_st
            and not feature.is_suspended
            and not feature.is_delisted
            and not feature.is_limit_up
        )

    def rank_candidates(
        self,
        features: Sequence[MATrendFeature],
        *,
        information_cutoff_at: datetime | None = None,
    ) -> list[EntrySignal]:
        scored: list[tuple[Decimal, MATrendFeature]] = []
        for feature in features:
            if not self.is_candidate(feature, information_cutoff_at=information_cutoff_at):
                continue
            assert feature.close is not None
            assert feature.short_ma is not None
            assert feature.long_ma is not None
            score = feature.close / feature.short_ma - 1
            score += feature.short_ma / feature.long_ma - 1
            scored.append((score, feature))
        scored.sort(key=lambda item: (-item[0], item[1].symbol))
        return [
            EntrySignal(feature.symbol, feature.trade_date, score, index, feature.industry)
            for index, (score, feature) in enumerate(scored, start=1)
        ]

    def select_candidates(
        self,
        features: Sequence[MATrendFeature],
        *,
        information_cutoff_at: datetime | None = None,
    ) -> list[EntrySignal]:
        ranked = self.rank_candidates(features, information_cutoff_at=information_cutoff_at)
        selected: list[EntrySignal] = []
        industries: dict[str | None, int] = {}
        for signal in ranked:
            if len(selected) >= self.config.max_positions:
                break
            count = industries.get(signal.industry, 0)
            if count >= self.config.max_per_industry:
                continue
            industries[signal.industry] = count + 1
            selected.append(
                EntrySignal(
                    signal.symbol,
                    signal.trade_date,
                    signal.score,
                    len(selected) + 1,
                    signal.industry,
                )
            )
        return selected

    generate_signals = select_candidates

    def exit_signal(
        self,
        feature: MATrendFeature,
        *,
        held_trading_days: int,
        next_trade_date: date,
    ) -> ExitSignal | None:
        reasons: list[str] = []
        if (
            feature.close is not None
            and feature.short_ma is not None
            and feature.close < feature.short_ma
        ):
            reasons.append("CLOSE_BELOW_SHORT_MA")
        if held_trading_days >= self.config.max_holding_days:
            reasons.append("MAX_HOLDING_DAYS")
        return (
            ExitSignal(
                feature.symbol,
                feature.trade_date,
                next_trade_date,
                reasons[0],
                tuple(reasons),
            )
            if reasons
            else None
        )


__all__ = [
    "MATrendConfig",
    "MATrendFeature",
    "MATrendStrategy",
    "build_ma_feature",
]
