"""Deterministic backtest snapshots and a minimal bar replay runner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal

from app.domain.backtest.metrics import Metrics, calculate_metrics
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import ExecutionSimulator, Fill, MarketBar, Order
from app.domain.portfolio.ledger import LedgerSnapshot, PortfolioLedger


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    start: date
    end: date
    initial_equity: Decimal = Decimal("20000")
    benchmark: str = "000300.SH"
    training_end: date | None = None
    validation_end: date | None = None
    execution_mode: str = "NEXT_OPEN_ADJUSTED"
    fill_mode: str = "FULL_OR_NONE"
    oos_start: date | None = None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    data_version: str
    strategy_version: str
    cost_version: str
    benchmark: str
    start: date
    end: date
    training_end: date | None
    validation_end: date | None
    input_hash: str
    initial_equity: Decimal
    execution_mode: str
    fill_mode: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    snapshot: RunSnapshot
    snapshot_hash: str
    equity_curve: tuple[Decimal, ...]
    metrics: Metrics
    ledger_snapshots: tuple[LedgerSnapshot, ...] = ()
    skipped_orders: tuple[SkippedOrder, ...] = ()
    fills: tuple[Fill, ...] = ()
    engine_version: str = "engine-v1"
    series: tuple[BacktestSeries, ...] = ()
    segment_metrics: tuple[SeriesSegmentMetrics, ...] = ()
    risk_state: str = "NORMAL"


@dataclass(frozen=True, slots=True)
class SkippedOrder:
    execution_date: date
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class BacktestSeries:
    series_code: str
    points: tuple[tuple[date, Decimal], ...]
    availability: str = "AVAILABLE"


@dataclass(frozen=True, slots=True)
class SeriesSegmentMetrics:
    series_code: str
    segment: str
    metrics: Metrics


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported value {type(value)!r}")


class BacktestRunner:
    def __init__(self, cost_model: CostModel | None = None) -> None:
        self.cost_model = cost_model or CostModel()

    def run(
        self,
        config: BacktestConfig,
        bars: Sequence[MarketBar],
        *,
        data_version: str,
        strategy_version: str,
        input_hash: str | None = None,
        orders: Mapping[date, Sequence[Order]] | None = None,
    ) -> BacktestResult:
        if config.execution_mode != "NEXT_OPEN_ADJUSTED" or config.fill_mode != "FULL_OR_NONE":
            raise ValueError("backtest requires NEXT_OPEN_ADJUSTED and FULL_OR_NONE")
        ordered = sorted(
            (bar for bar in bars if config.start <= bar.trade_date <= config.end),
            key=lambda bar: (bar.trade_date, bar.symbol),
        )
        order_payload = {
            trade_date.isoformat(): [
                {
                    "side": order.side,
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                    "fill_mode": order.fill_mode,
                    "signal_date": order.signal_date,
                }
                for order in day_orders
            ]
            for trade_date, day_orders in sorted((orders or {}).items())
        }
        raw_payload = {
            "bars": [
                {
                    "symbol": bar.symbol,
                    "trade_date": bar.trade_date,
                    "open": bar.open,
                    "low": bar.low,
                    "high": bar.high,
                    "close": bar.close,
                    "is_suspended": bar.is_suspended,
                    "limit_up": bar.limit_up,
                    "limit_down": bar.limit_down,
                    "close_limit_up": bar.close_limit_up,
                    "close_limit_down": bar.close_limit_down,
                    "available_quantity": bar.available_quantity,
                }
                for bar in ordered
            ],
            "orders": order_payload,
        }
        raw = (
            input_hash
            or hashlib.sha256(
                json.dumps(raw_payload, default=_json_default, sort_keys=True).encode()
            ).hexdigest()
        )
        snapshot = RunSnapshot(
            data_version,
            strategy_version,
            self.cost_model.version,
            config.benchmark,
            config.start,
            config.end,
            config.training_end,
            config.validation_end,
            raw,
            config.initial_equity,
            config.execution_mode,
            config.fill_mode,
        )
        snapshot_hash = hashlib.sha256(
            json.dumps(asdict(snapshot), default=_json_default, sort_keys=True).encode()
        ).hexdigest()
        ledger = PortfolioLedger(initial_cash=config.initial_equity)
        simulator = ExecutionSimulator(self.cost_model)
        by_date: dict[date, list[MarketBar]] = {}
        for bar in ordered:
            by_date.setdefault(bar.trade_date, []).append(bar)
        equity_values = [config.initial_equity]
        ledger_snapshots: list[LedgerSnapshot] = []
        skipped_orders: list[SkippedOrder] = []
        fills: list[Fill] = []
        last_closes: dict[str, Decimal] = {}
        for trade_date, day_bars in sorted(by_date.items()):
            ledger.settle_all()
            bars_by_symbol = {bar.symbol: bar for bar in day_bars}
            for day_bar in day_bars:
                if day_bar.close is not None:
                    last_closes[day_bar.symbol] = day_bar.close
            for order in (orders or {}).get(trade_date, ()):
                order_bar = bars_by_symbol.get(order.symbol)
                if order_bar is None:
                    skipped_orders.append(
                        SkippedOrder(trade_date, order.symbol, "MISSING_EXECUTION_BAR")
                    )
                    continue
                if order.signal_date is None:
                    skipped_orders.append(
                        SkippedOrder(trade_date, order.symbol, "MISSING_SIGNAL_DATE")
                    )
                    continue
                if order.signal_date >= trade_date:
                    skipped_orders.append(
                        SkippedOrder(trade_date, order.symbol, "SIGNAL_DATE_NOT_BEFORE_EXECUTION")
                    )
                    continue
                backtest_order = replace(order, fill_mode=config.fill_mode)
                fill = simulator.execute(backtest_order, order_bar)
                fill = replace(
                    fill,
                    execution_date=trade_date,
                    signal_date=order.signal_date,
                    industry=order.industry,
                )
                fills.append(fill)
                if (
                    fill.status in {"FILLED", "PARTIALLY_FILLED"}
                    and fill.price is not None
                    and fill.costs is not None
                ):
                    ledger.apply_fill(
                        fill.side, fill.symbol, fill.quantity, fill.price, fill.costs.total
                    )
            day_snapshot = ledger.snapshot(prices=last_closes)
            ledger_snapshots.append(day_snapshot)
            equity_values.append(day_snapshot.equity)
        equity = tuple(equity_values)
        return BacktestResult(
            snapshot,
            snapshot_hash,
            equity,
            calculate_metrics(equity, fills=fills, trade_dates=tuple(sorted(by_date))),
            tuple(ledger_snapshots),
            tuple(skipped_orders),
            tuple(fills),
        )
