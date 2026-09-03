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
from app.domain.execution.simulator import ExecutionSimulator, MarketBar, Order
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


@dataclass(frozen=True, slots=True)
class SkippedOrder:
    execution_date: date
    symbol: str
    reason: str


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
        ordered = sorted((bar for bar in bars if config.start <= bar.trade_date <= config.end), key=lambda bar: (bar.trade_date, bar.symbol))
        order_payload = {
            trade_date.isoformat(): [asdict(order) for order in day_orders]
            for trade_date, day_orders in sorted((orders or {}).items())
        }
        raw_payload = {"bars": [asdict(bar) for bar in ordered], "orders": order_payload}
        raw = input_hash or hashlib.sha256(json.dumps(raw_payload, default=_json_default, sort_keys=True).encode()).hexdigest()
        snapshot = RunSnapshot(data_version, strategy_version, self.cost_model.version, config.benchmark, config.start, config.end, config.training_end, config.validation_end, raw, config.initial_equity, config.execution_mode, config.fill_mode)
        snapshot_hash = hashlib.sha256(json.dumps(asdict(snapshot), default=_json_default, sort_keys=True).encode()).hexdigest()
        ledger = PortfolioLedger(initial_cash=config.initial_equity)
        simulator = ExecutionSimulator(self.cost_model)
        by_date: dict[date, list[MarketBar]] = {}
        for bar in ordered:
            by_date.setdefault(bar.trade_date, []).append(bar)
        equity_values = [config.initial_equity]
        ledger_snapshots: list[LedgerSnapshot] = []
        skipped_orders: list[SkippedOrder] = []
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
                    skipped_orders.append(SkippedOrder(trade_date, order.symbol, "MISSING_EXECUTION_BAR"))
                    continue
                if order.signal_date is None:
                    skipped_orders.append(SkippedOrder(trade_date, order.symbol, "MISSING_SIGNAL_DATE"))
                    continue
                if order.signal_date >= trade_date:
                    skipped_orders.append(SkippedOrder(trade_date, order.symbol, "SIGNAL_DATE_NOT_BEFORE_EXECUTION"))
                    continue
                backtest_order = replace(order, fill_mode=config.fill_mode)
                fill = simulator.execute(backtest_order, order_bar)
                if fill.status in {"FILLED", "PARTIALLY_FILLED"} and fill.price is not None and fill.costs is not None:
                    ledger.apply_fill(fill.side, fill.symbol, fill.quantity, fill.price, fill.costs.total)
            day_snapshot = ledger.snapshot(prices=last_closes)
            ledger_snapshots.append(day_snapshot)
            equity_values.append(day_snapshot.equity)
        equity = tuple(equity_values)
        return BacktestResult(snapshot, snapshot_hash, equity, calculate_metrics(equity), tuple(ledger_snapshots), tuple(skipped_orders))
