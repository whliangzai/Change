"""Causal, deterministic strong-trend continuation strategy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.domain.causality import available_at_or_before
from app.domain.strategy.features import FeatureSnapshot


@dataclass(frozen=True, slots=True)
class StrongTrendConfig:
    max_positions: int = 4
    max_per_industry: int = 2
    min_return_5d: Decimal = Decimal("0.02")
    max_return_5d: Decimal = Decimal("0.12")
    min_amount_ratio: Decimal = Decimal("1.2")
    max_amount_ratio: Decimal = Decimal("3.0")
    max_holding_days: int = 3
    drawdown_exit: Decimal = Decimal("0.04")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    symbol: str
    trade_date: date
    score: Decimal
    rank: int
    industry: str | None


@dataclass(frozen=True, slots=True)
class ExitSignal:
    symbol: str
    trade_date: date
    plan_date: date
    reason: str
    reasons: tuple[str, ...] = ()


def _rank(values: list[Decimal], value: Decimal) -> Decimal:
    """Return a descending percentile rank, with deterministic tie handling."""
    ordered = sorted(values, reverse=True)
    positions = [index + 1 for index, item in enumerate(ordered) if item == value]
    average = Decimal(sum(positions)) / Decimal(len(positions))
    if len(ordered) == 1:
        return Decimal("1")
    return Decimal(len(ordered) - average) / Decimal(len(ordered) - 1)


class StrongTrendStrategy:
    def __init__(self, config: StrongTrendConfig | None = None) -> None:
        self.config = config or StrongTrendConfig()

    def is_candidate(
        self,
        feature: FeatureSnapshot,
        *,
        information_cutoff_at: datetime | None = None,
    ) -> bool:
        if not available_at_or_before(feature.available_at, information_cutoff_at):
            return False
        if any(
            value is None
            for value in (
                feature.close,
                feature.ma10,
                feature.market_close,
                feature.market_ma20,
                feature.return_5d,
                feature.return_20d,
                feature.amount_ratio,
            )
        ):
            return False
        assert feature.close is not None
        assert feature.ma10 is not None
        assert feature.market_close is not None
        assert feature.market_ma20 is not None
        assert feature.return_5d is not None
        assert feature.amount_ratio is not None
        return (
            feature.trade_date
            <= (information_cutoff_at.date() if information_cutoff_at else feature.trade_date)
            and feature.market_close > feature.market_ma20
            and feature.close > feature.ma10
            and self.config.min_return_5d <= feature.return_5d <= self.config.max_return_5d
            and self.config.min_amount_ratio <= feature.amount_ratio <= self.config.max_amount_ratio
            and feature.reliably_buyable
            and not feature.is_st
            and not feature.is_suspended
            and not feature.is_delisted
            and not feature.is_limit_up
        )

    def rank_candidates(
        self,
        features: Iterable[FeatureSnapshot],
        *,
        information_cutoff_at: datetime | None = None,
    ) -> list[EntrySignal]:
        eligible = [
            feature
            for feature in features
            if self.is_candidate(feature, information_cutoff_at=information_cutoff_at)
        ]
        if not eligible:
            return []
        returns5 = [feature.return_5d for feature in eligible if feature.return_5d is not None]
        returns20 = [feature.return_20d for feature in eligible if feature.return_20d is not None]
        ratios = [feature.amount_ratio for feature in eligible if feature.amount_ratio is not None]
        scored: list[tuple[Decimal, FeatureSnapshot]] = []
        for feature in eligible:
            assert (
                feature.return_5d is not None
                and feature.return_20d is not None
                and feature.amount_ratio is not None
            )
            score = Decimal("0.50") * _rank(returns5, feature.return_5d)
            score += Decimal("0.25") * _rank(returns20, feature.return_20d)
            score += Decimal("0.25") * _rank(ratios, feature.amount_ratio)
            scored.append((score, feature))
        scored.sort(key=lambda item: (-item[0], item[1].symbol))
        return [
            EntrySignal(feature.symbol, feature.trade_date, score, index, feature.industry)
            for index, (score, feature) in enumerate(scored, start=1)
        ]

    def select_candidates(
        self,
        features: Iterable[FeatureSnapshot],
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
        feature: FeatureSnapshot,
        *,
        entry_price: Decimal,
        held_trading_days: int,
        rank: int,
        candidate_count: int,
        market_closed_streak: int,
        next_trade_date: date,
        eligible: bool = True,
    ) -> ExitSignal | None:
        reasons: list[str] = []
        if held_trading_days >= self.config.max_holding_days:
            reasons.append("MAX_HOLDING_DAYS")
        if not eligible:
            reasons.append("NOT_ELIGIBLE")
        elif candidate_count > 0 and rank > Decimal(candidate_count) / Decimal("2"):
            reasons.append("RANK_OUT_OF_POOL")
        if feature.close is not None and feature.close <= entry_price * (
            Decimal("1") - self.config.drawdown_exit
        ):
            reasons.append("ENTRY_DRAWDOWN")
        if market_closed_streak >= 2:
            reasons.append("MARKET_SWITCH_OFF")
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
