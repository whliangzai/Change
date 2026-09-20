"""Stateful, causal engine-v2 backtest event loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from app.domain.backtest.metrics import calculate_metrics
from app.domain.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    BacktestSeries,
    RunSnapshot,
    SeriesSegmentMetrics,
    SkippedOrder,
    _json_default,
)
from app.domain.causality import as_utc, available_at_or_before
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import ExecutionSimulator, Fill, MarketBar, Order
from app.domain.portfolio.ledger import PortfolioLedger
from app.domain.risk.allocation import AllocationLimits, Position, allocate_quantity
from app.domain.risk.drawdown import DrawdownMonitor
from app.domain.strategy.features import DailyBar as StrategyDailyBar
from app.domain.strategy.features import FeatureSnapshot, build_feature_snapshot
from app.domain.strategy.ma_trend import MATrendFeature, MATrendStrategy, build_ma_feature
from app.domain.strategy.registry import StrategyType, get_strategy_definition
from app.domain.strategy.strong_trend import StrongTrendStrategy


class BenchmarkUnavailableError(ValueError):
    """Raised when an enabled synthetic benchmark cannot be computed causally."""


@dataclass(frozen=True, slots=True)
class _EntryState:
    price: Decimal
    trade_date: date
    trade_index: int
    industry: str | None


_DEFAULT_RULE_CONFIG: dict[str, object] = {
    "max_investment_ratio": "0.70",
    "max_positions": 4,
    "max_position_ratio": "0.20",
    "max_industry_ratio": "0.35",
    "target_position_value": "3500",
    "lot_size": 100,
    "drawdown_stop_new": "0.06",
    "drawdown_review_required": "0.08",
    "automatic_risk_recovery": False,
}


def _decimal_rule(config: Mapping[str, object], key: str) -> Decimal:
    try:
        return Decimal(str(config[key]))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"rule configuration has invalid {key}") from exc


def _allocation_limits(
    initial_equity: Decimal, rule_config: Mapping[str, object]
) -> AllocationLimits:
    if rule_config.get("automatic_risk_recovery") is not False:
        raise ValueError("engine-v2 requires automatic_risk_recovery=false")
    try:
        max_positions = int(str(rule_config["max_positions"]))
        lot_size = int(str(rule_config["lot_size"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("rule configuration has invalid integer limits") from exc
    return AllocationLimits(
        initial_equity=initial_equity,
        max_investment_ratio=_decimal_rule(rule_config, "max_investment_ratio"),
        max_positions=max_positions,
        target_position_value=_decimal_rule(rule_config, "target_position_value"),
        max_position_ratio=_decimal_rule(rule_config, "max_position_ratio"),
        max_industry_ratio=_decimal_rule(rule_config, "max_industry_ratio"),
        per_trade_risk_ratio=Decimal(str(rule_config.get("per_trade_risk_ratio", "0.01"))),
        lot_size=lot_size,
    )


class StatefulBacktestRunner:
    engine_version = "engine-v2"

    def __init__(
        self,
        cost_model: CostModel | None = None,
        limits: AllocationLimits | None = None,
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.limits = limits

    def run(
        self,
        config: BacktestConfig,
        bars: tuple[MarketBar, ...],
        *,
        data_version: str,
        strategy_version: str,
        strategy_type: str,
        strategy_parameters: dict[str, object],
        information_cutoff_at: datetime,
        strategy_implementation_version: str,
        trading_dates: tuple[date, ...],
        rule_version: str = "rule_v2",
        rule_config: Mapping[str, object] | None = None,
        secondary_benchmark: str | None = "000001.SH",
        universe_benchmark_enabled: bool = True,
        suspended_symbols_by_date: Mapping[date, frozenset[str]] | None = None,
        data_batches: tuple[Mapping[str, object], ...] = (),
        cost_config: Mapping[str, object] | None = None,
        cost_config_hash: str | None = None,
    ) -> BacktestResult:
        if config.execution_mode != "NEXT_OPEN_ADJUSTED" or config.fill_mode != "FULL_OR_NONE":
            raise ValueError("backtest requires NEXT_OPEN_ADJUSTED and FULL_OR_NONE")
        definition = get_strategy_definition(strategy_type)
        if not definition.supports_backtest:
            raise ValueError(f"strategy {strategy_type} is not approved for backtest")
        if definition.implementation_version != strategy_implementation_version:
            raise ValueError(
                "requested strategy implementation is unavailable: "
                f"{strategy_implementation_version}"
            )
        strategy = definition.build(strategy_parameters)
        effective_rule = dict(rule_config or _DEFAULT_RULE_CONFIG)
        effective_cost = dict(cost_config or self.cost_model.configuration())
        effective_cost_hash = hashlib.sha256(
            json.dumps(
                effective_cost,
                default=_json_default,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if cost_config_hash is not None and cost_config_hash != effective_cost_hash:
            raise ValueError("cost configuration hash does not match its content")
        limits = self.limits or _allocation_limits(config.initial_equity, effective_rule)
        ordered = tuple(
            sorted(
                (
                    bar
                    for bar in bars
                    if bar.trade_date <= config.end
                    and available_at_or_before(bar.available_at, information_cutoff_at)
                ),
                key=lambda item: (item.trade_date, item.symbol),
            )
        )
        trade_dates = tuple(
            sorted(
                {
                    trade_date
                    for trade_date in trading_dates
                    if config.start <= trade_date <= config.end
                }
            )
        )
        if not trade_dates:
            raise BenchmarkUnavailableError("authoritative trading calendar has no open dates")

        bars_by_date: dict[date, dict[str, MarketBar]] = {}
        for bar in ordered:
            bars_by_date.setdefault(bar.trade_date, {})[bar.symbol] = bar
        missing_primary = [
            trade_date
            for trade_date in trade_dates
            if (primary := bars_by_date.get(trade_date, {}).get(config.benchmark)) is None
            or primary.close is None
        ]
        if missing_primary:
            raise BenchmarkUnavailableError(
                f"BENCHMARK_PRIMARY is missing price on {missing_primary[0]}"
            )
        strategy_bars = tuple(
            StrategyDailyBar(
                symbol=bar.symbol,
                trade_date=bar.trade_date,
                close=bar.close,
                amount=bar.amount or Decimal("0"),
                available_at=bar.available_at,
                is_suspended=bar.is_suspended,
                is_st=bar.is_st,
                is_delisted=bar.is_delisted,
                is_limit_up=bar.close_limit_up is not False,
                industry=bar.industry,
                list_date=bar.list_date,
            )
            for bar in ordered
            if bar.close is not None
        )
        market_bars = tuple(bar for bar in strategy_bars if bar.symbol == config.benchmark)
        excluded = {config.benchmark}
        if secondary_benchmark:
            excluded.add(secondary_benchmark)
        candidate_bars = tuple(bar for bar in ordered if bar.symbol not in excluded)
        unknown_eligibility = next(
            (bar for bar in candidate_bars if bar.universe_eligible is None), None
        )
        if unknown_eligibility is not None:
            raise BenchmarkUnavailableError(
                "historical universe eligibility is unavailable for "
                f"{unknown_eligibility.symbol} on {unknown_eligibility.trade_date}"
            )
        eligible_by_date = {
            trade_date: frozenset(
                bar.symbol
                for bar in bars_by_date.get(trade_date, {}).values()
                if bar.symbol not in excluded and bar.universe_eligible is True
            )
            for trade_date in trade_dates
        }
        symbols = tuple(
            sorted({bar.symbol for bar in candidate_bars if bar.universe_eligible is True})
        )

        ledger = PortfolioLedger(initial_cash=config.initial_equity)
        simulator = ExecutionSimulator(self.cost_model)
        drawdown = DrawdownMonitor(
            initial_equity=config.initial_equity,
            warning_threshold=_decimal_rule(effective_rule, "drawdown_stop_new"),
            stop_threshold=_decimal_rule(effective_rule, "drawdown_review_required"),
        )
        pending: dict[date, list[Order]] = {}
        entries: dict[str, _EntryState] = {}
        last_closes: dict[str, Decimal] = {}
        ledger_snapshots = []
        fills: list[Fill] = []
        skipped: list[SkippedOrder] = []
        market_closed_streak = 0

        for trade_index, trade_date in enumerate(trade_dates):
            ledger.settle_all()
            day_bars = bars_by_date.get(trade_date, {})
            for order in sorted(
                pending.pop(trade_date, []),
                key=lambda item: (0 if item.side.upper() == "SELL" else 1, item.symbol),
            ):
                execution_bar = day_bars.get(order.symbol)
                if execution_bar is None:
                    skipped.append(SkippedOrder(trade_date, order.symbol, "MISSING_EXECUTION_BAR"))
                    continue
                simulated = simulator.execute(
                    replace(order, fill_mode=config.fill_mode), execution_bar
                )
                fill = replace(
                    simulated,
                    execution_date=trade_date,
                    signal_date=order.signal_date,
                    industry=order.industry,
                    trigger_reasons=order.reasons,
                )
                if (
                    fill.status in {"FILLED", "PARTIALLY_FILLED"}
                    and fill.price is not None
                    and fill.costs is not None
                ):
                    fill_price = fill.price
                    fill_costs = fill.costs
                    total = fill_price * Decimal(fill.quantity) + fill_costs.total
                    if fill.side == "BUY" and total > ledger.cash:
                        fill = replace(
                            fill,
                            status="UNFILLED",
                            quantity=0,
                            price=None,
                            costs=None,
                            reason="INSUFFICIENT_CASH",
                            unfilled_quantity=order.quantity,
                        )
                    else:
                        ledger.apply_fill(
                            fill.side,
                            fill.symbol,
                            fill.quantity,
                            fill_price,
                            fill_costs.total,
                        )
                        if fill.side == "BUY":
                            entries[fill.symbol] = _EntryState(
                                fill_price, trade_date, trade_index, fill.industry
                            )
                        elif ledger.position(fill.symbol).quantity == 0:
                            entries.pop(fill.symbol, None)
                fills.append(fill)

            for symbol, bar in day_bars.items():
                if bar.close is not None:
                    last_closes[symbol] = bar.close
            snapshot = ledger.snapshot(prices=last_closes)
            ledger_snapshots.append(snapshot)
            risk_state = drawdown.observe(snapshot.equity)

            if trade_index == len(trade_dates) - 1:
                continue
            next_trade_date = trade_dates[trade_index + 1]
            features = self._features(
                strategy_type,
                strategy,
                strategy_bars,
                market_bars,
                symbols,
                trade_date,
                information_cutoff_at,
            )
            eligible_today = eligible_by_date.get(trade_date, frozenset())
            eligible_features = tuple(
                feature for feature in features if feature.symbol in eligible_today
            )
            if isinstance(strategy, StrongTrendStrategy):
                strong_features = cast(tuple[FeatureSnapshot, ...], eligible_features)
                ranked = strategy.rank_candidates(
                    strong_features, information_cutoff_at=information_cutoff_at
                )
            else:
                ma_features = cast(tuple[MATrendFeature, ...], eligible_features)
                ranked = strategy.rank_candidates(
                    ma_features, information_cutoff_at=information_cutoff_at
                )
            ranks = {signal.symbol: signal.rank for signal in ranked}

            if strategy_type == StrategyType.STRONG_TREND.value:
                first = next(iter(features), None)
                if isinstance(first, FeatureSnapshot) and first.market_close is not None:
                    if first.market_ma20 is not None and first.market_close > first.market_ma20:
                        market_closed_streak = 0
                    else:
                        market_closed_streak += 1

            exiting: set[str] = set()
            for position in ledger.positions:
                feature = next((item for item in features if item.symbol == position.symbol), None)
                entry = entries.get(position.symbol)
                if feature is None or entry is None:
                    continue
                held_days = trade_index - entry.trade_index + 1
                if isinstance(strategy, StrongTrendStrategy):
                    assert isinstance(feature, FeatureSnapshot)
                    exit_signal = strategy.exit_signal(
                        feature,
                        entry_price=entry.price,
                        held_trading_days=held_days,
                        rank=ranks.get(position.symbol, len(ranked) + 1),
                        candidate_count=len(ranked),
                        market_closed_streak=market_closed_streak,
                        next_trade_date=next_trade_date,
                        eligible=position.symbol in eligible_today and position.symbol in ranks,
                    )
                else:
                    assert isinstance(strategy, MATrendStrategy)
                    assert isinstance(feature, MATrendFeature)
                    exit_signal = strategy.exit_signal(
                        feature,
                        held_trading_days=held_days,
                        next_trade_date=next_trade_date,
                    )
                if exit_signal is None:
                    continue
                exiting.add(position.symbol)
                pending.setdefault(next_trade_date, []).append(
                    Order(
                        "SELL",
                        position.symbol,
                        position.quantity,
                        signal_date=trade_date,
                        industry=entry.industry,
                        reasons=exit_signal.reasons or (exit_signal.reason,),
                    )
                )

            if isinstance(strategy, StrongTrendStrategy):
                selected = strategy.select_candidates(
                    cast(tuple[FeatureSnapshot, ...], eligible_features),
                    information_cutoff_at=information_cutoff_at,
                )
            else:
                selected = strategy.select_candidates(
                    cast(tuple[MATrendFeature, ...], eligible_features),
                    information_cutoff_at=information_cutoff_at,
                )
            virtual_cash = ledger.cash
            projected = self._positions(ledger, last_closes, entries)
            held = {position.symbol for position in projected}
            max_per_industry = strategy.config.max_per_industry
            industry_counts: dict[str, int] = {}
            for projected_position in projected:
                if (
                    projected_position.quantity > 0
                    and projected_position.symbol not in exiting
                    and projected_position.industry is not None
                ):
                    industry_counts[projected_position.industry] = (
                        industry_counts.get(projected_position.industry, 0) + 1
                    )
            for entry_signal in selected:
                if entry_signal.symbol in held or entry_signal.symbol in exiting:
                    continue
                if (
                    entry_signal.industry is not None
                    and industry_counts.get(entry_signal.industry, 0) >= max_per_industry
                ):
                    continue
                feature = next(item for item in features if item.symbol == entry_signal.symbol)
                reference = feature.close
                if reference is None or reference <= 0:
                    continue
                quantity = allocate_quantity(
                    price=reference,
                    cash=virtual_cash,
                    equity=snapshot.equity,
                    positions=projected,
                    industry=entry_signal.industry,
                    limits=limits,
                    symbol=entry_signal.symbol,
                    cost_model=self.cost_model,
                    drawdown_state=risk_state,
                )
                if quantity <= 0:
                    continue
                estimated = reference * Decimal(quantity)
                estimated += self.cost_model.calculate(
                    side="BUY", quantity=quantity, price=reference
                ).total
                virtual_cash -= estimated
                projected.append(
                    Position(
                        entry_signal.symbol,
                        quantity,
                        reference * Decimal(quantity),
                        entry_signal.industry,
                    )
                )
                held.add(entry_signal.symbol)
                if entry_signal.industry is not None:
                    industry_counts[entry_signal.industry] = (
                        industry_counts.get(entry_signal.industry, 0) + 1
                    )
                pending.setdefault(next_trade_date, []).append(
                    Order(
                        "BUY",
                        entry_signal.symbol,
                        quantity,
                        signal_date=trade_date,
                        industry=entry_signal.industry,
                        reasons=("ENTRY_SIGNAL",),
                    )
                )

        strategy_points = tuple(
            (trade_date, snapshot.equity)
            for trade_date, snapshot in zip(trade_dates, ledger_snapshots, strict=True)
        )
        series = [BacktestSeries("STRATEGY", strategy_points)]
        series.append(
            self._market_series(
                "BENCHMARK_PRIMARY",
                config.benchmark,
                trade_dates,
                bars_by_date,
                config.initial_equity,
            )
        )
        if secondary_benchmark:
            secondary = self._optional_market_series(
                "BENCHMARK_SECONDARY",
                secondary_benchmark,
                trade_dates,
                bars_by_date,
                config.initial_equity,
            )
            if secondary is not None:
                series.append(secondary)
        if universe_benchmark_enabled:
            series.append(
                self._universe_series(
                    trade_dates,
                    bars_by_date,
                    eligible_by_date,
                    config.initial_equity,
                    suspended_symbols_by_date or {},
                )
            )

        equity_curve = (config.initial_equity,) + tuple(
            snapshot.equity for snapshot in ledger_snapshots
        )
        metrics = calculate_metrics(equity_curve, fills=fills, trade_dates=trade_dates)
        segment_metrics = self._segment_metrics(series, config, fills)
        input_payload = {
            "engine_version": self.engine_version,
            "strategy_type": strategy_type,
            "strategy_parameters": strategy_parameters,
            "strategy_implementation_version": strategy_implementation_version,
            "information_cutoff_at": as_utc(information_cutoff_at).isoformat(),
            "backtest_config": asdict(config),
            "trading_dates": trading_dates,
            "rule_version": rule_version,
            "rule_config": effective_rule,
            "secondary_benchmark": secondary_benchmark,
            "universe_benchmark_enabled": universe_benchmark_enabled,
            "cost_config": effective_cost,
            "cost_config_hash": effective_cost_hash,
            "data_batches": data_batches,
            "suspended_symbols_by_date": {
                trade_date.isoformat(): sorted(symbols)
                for trade_date, symbols in sorted(
                    (suspended_symbols_by_date or {}).items(), key=lambda item: item[0]
                )
            },
            "bars": [asdict(bar) for bar in ordered],
        }
        input_hash = hashlib.sha256(
            json.dumps(input_payload, default=_json_default, sort_keys=True).encode()
        ).hexdigest()
        run_snapshot = RunSnapshot(
            data_version=data_version,
            strategy_version=strategy_version,
            cost_version=self.cost_model.version,
            benchmark=config.benchmark,
            start=config.start,
            end=config.end,
            training_end=config.training_end,
            validation_end=config.validation_end,
            input_hash=input_hash,
            initial_equity=config.initial_equity,
            execution_mode=config.execution_mode,
            fill_mode=config.fill_mode,
        )
        snapshot_hash = hashlib.sha256(
            json.dumps(
                {
                    **asdict(run_snapshot),
                    "engine_version": self.engine_version,
                    "strategy_type": strategy_type,
                    "effective_parameters": strategy_parameters,
                    "strategy_implementation_version": strategy_implementation_version,
                    "rule_version": rule_version,
                    "rule_config": effective_rule,
                    "oos_start": config.oos_start,
                    "secondary_benchmark": secondary_benchmark,
                    "universe_benchmark_enabled": universe_benchmark_enabled,
                    "cost_config": effective_cost,
                    "cost_config_hash": effective_cost_hash,
                    "data_batches": data_batches,
                    "suspended_symbols_by_date": {
                        trade_date.isoformat(): sorted(symbols)
                        for trade_date, symbols in sorted(
                            (suspended_symbols_by_date or {}).items(), key=lambda item: item[0]
                        )
                    },
                },
                default=_json_default,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return BacktestResult(
            snapshot=run_snapshot,
            snapshot_hash=snapshot_hash,
            equity_curve=equity_curve,
            metrics=metrics,
            ledger_snapshots=tuple(ledger_snapshots),
            skipped_orders=tuple(skipped),
            fills=tuple(fills),
            engine_version=self.engine_version,
            series=tuple(series),
            segment_metrics=segment_metrics,
            risk_state=drawdown.state.value,
        )

    @staticmethod
    def _features(
        strategy_type: str,
        strategy: StrongTrendStrategy | MATrendStrategy,
        bars: tuple[StrategyDailyBar, ...],
        market_bars: tuple[StrategyDailyBar, ...],
        symbols: tuple[str, ...],
        trade_date: date,
        information_cutoff_at: datetime,
    ) -> tuple[FeatureSnapshot | MATrendFeature, ...]:
        if strategy_type == StrategyType.STRONG_TREND.value:
            return tuple(
                build_feature_snapshot(
                    bars,
                    market_bars,
                    symbol=symbol,
                    trade_date=trade_date,
                    information_cutoff_at=information_cutoff_at,
                )
                for symbol in symbols
            )
        assert isinstance(strategy, MATrendStrategy)
        return tuple(
            build_ma_feature(
                bars,
                market_bars,
                symbol=symbol,
                trade_date=trade_date,
                short_window=strategy.config.short_window,
                long_window=strategy.config.long_window,
                information_cutoff_at=information_cutoff_at,
            )
            for symbol in symbols
        )

    @staticmethod
    def _positions(
        ledger: PortfolioLedger,
        prices: dict[str, Decimal],
        entries: dict[str, _EntryState],
    ) -> list[Position]:
        return [
            Position(
                item.symbol,
                item.quantity,
                prices.get(item.symbol, item.cost_basis / Decimal(item.quantity))
                * Decimal(item.quantity),
                entries[item.symbol].industry if item.symbol in entries else None,
            )
            for item in ledger.positions
        ]

    @staticmethod
    def _market_series(
        code: str,
        symbol: str,
        trade_dates: tuple[date, ...],
        bars_by_date: dict[date, dict[str, MarketBar]],
        initial_equity: Decimal,
    ) -> BacktestSeries:
        closes: list[Decimal] = []
        for trade_date in trade_dates:
            bar = bars_by_date.get(trade_date, {}).get(symbol)
            if bar is None or bar.close is None:
                raise BenchmarkUnavailableError(f"{code} is missing price on {trade_date}")
            closes.append(bar.close)
        first = closes[0]
        return BacktestSeries(
            code,
            tuple(
                (trade_date, initial_equity * close / first)
                for trade_date, close in zip(trade_dates, closes, strict=True)
            ),
        )

    @classmethod
    def _optional_market_series(
        cls,
        code: str,
        symbol: str,
        trade_dates: tuple[date, ...],
        bars_by_date: dict[date, dict[str, MarketBar]],
        initial_equity: Decimal,
    ) -> BacktestSeries | None:
        if not all(
            bars_by_date.get(trade_date, {}).get(symbol) is not None
            and bars_by_date[trade_date][symbol].close is not None
            for trade_date in trade_dates
        ):
            return None
        return cls._market_series(code, symbol, trade_dates, bars_by_date, initial_equity)

    @staticmethod
    def _universe_series(
        trade_dates: tuple[date, ...],
        bars_by_date: dict[date, dict[str, MarketBar]],
        eligible_by_date: Mapping[date, frozenset[str]],
        initial_equity: Decimal,
        suspended_symbols_by_date: Mapping[date, frozenset[str]],
    ) -> BacktestSeries:
        value = initial_equity
        points: list[tuple[date, Decimal]] = [(trade_dates[0], value)]
        for previous_date, trade_date in zip(trade_dates, trade_dates[1:], strict=False):
            previous_bars = bars_by_date.get(previous_date, {})
            current_bars = bars_by_date.get(trade_date, {})
            members = sorted(eligible_by_date.get(previous_date, frozenset()))
            if not members:
                raise BenchmarkUnavailableError(
                    f"UNIVERSE_EQUAL_WEIGHT has no T-1 constituents on {trade_date}"
                )
            returns: list[Decimal] = []
            for symbol in members:
                previous = previous_bars[symbol]
                current = current_bars.get(symbol)
                assert previous.close is not None
                if current is None:
                    if symbol in suspended_symbols_by_date.get(trade_date, frozenset()):
                        returns.append(Decimal("0"))
                        continue
                    raise BenchmarkUnavailableError(
                        f"UNIVERSE_EQUAL_WEIGHT missing non-suspended price for {symbol} on {trade_date}"
                    )
                if current.is_suspended:
                    returns.append(Decimal("0"))
                elif current.close is None:
                    raise BenchmarkUnavailableError(
                        f"UNIVERSE_EQUAL_WEIGHT missing non-suspended price for {symbol} on {trade_date}"
                    )
                else:
                    returns.append(current.close / previous.close - 1)
            value *= Decimal("1") + sum(returns, Decimal("0")) / Decimal(len(returns))
            points.append((trade_date, value))
        return BacktestSeries(
            "UNIVERSE_EQUAL_WEIGHT",
            tuple(points),
            "SYNTHETIC_NOT_INVESTABLE",
        )

    @staticmethod
    def _segment_metrics(
        series: list[BacktestSeries],
        config: BacktestConfig,
        fills: list[Fill],
    ) -> tuple[SeriesSegmentMetrics, ...]:
        result: list[SeriesSegmentMetrics] = []
        for item in series:
            segments: dict[str, tuple[tuple[date, Decimal], ...]] = {"FULL": item.points}
            if config.training_end is not None:
                segments["TRAIN"] = tuple(
                    point for point in item.points if point[0] <= config.training_end
                )
            if config.training_end is not None and config.validation_end is not None:
                validation_points = tuple(
                    point
                    for point in item.points
                    if config.training_end < point[0] <= config.validation_end
                )
                validation_anchor = next(
                    (point for point in reversed(item.points) if point[0] <= config.training_end),
                    None,
                )
                segments["VALIDATION"] = (
                    (validation_anchor,) if validation_anchor is not None else ()
                ) + validation_points
            if config.oos_start is not None:
                oos_points = tuple(point for point in item.points if point[0] >= config.oos_start)
                oos_anchor = next(
                    (point for point in reversed(item.points) if point[0] < config.oos_start),
                    None,
                )
                segments["OOS"] = ((oos_anchor,) if oos_anchor is not None else ()) + oos_points
            for segment, points in segments.items():
                values = tuple(value for _, value in points)
                dates = tuple(trade_date for trade_date, _ in points[1:])
                point_dates = {
                    trade_date
                    for trade_date, _ in (points if segment in {"FULL", "TRAIN"} else points[1:])
                }
                segment_fills = (
                    [fill for fill in fills if fill.execution_date in point_dates]
                    if item.series_code == "STRATEGY"
                    else []
                )
                result.append(
                    SeriesSegmentMetrics(
                        item.series_code,
                        segment,
                        calculate_metrics(values, fills=segment_fills, trade_dates=dates),
                    )
                )
        return tuple(result)


__all__ = ["BenchmarkUnavailableError", "StatefulBacktestRunner"]
