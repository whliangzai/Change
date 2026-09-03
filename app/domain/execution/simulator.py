"""Deterministic next-open execution simulator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.execution.costs import CostBreakdown, CostModel


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    trade_date: date
    open: Decimal | None
    low: Decimal | None
    high: Decimal | None
    close: Decimal | None
    is_suspended: bool = False
    limit_up: bool = False
    limit_down: bool = False
    available_quantity: int | None = None


@dataclass(frozen=True, slots=True)
class Order:
    side: str
    symbol: str
    quantity: int
    fill_mode: str = "FULL_OR_NONE"
    signal_date: date | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    status: str
    side: str
    symbol: str
    quantity: int
    price: Decimal | None
    costs: CostBreakdown | None = None
    reason: str | None = None
    unfilled_quantity: int = 0


class ExecutionSimulator:
    def __init__(self, cost_model: CostModel | None = None) -> None:
        self.cost_model = cost_model or CostModel()

    def execute(self, order: Order, bar: MarketBar) -> Fill:
        side = order.side.upper()
        if side not in {"BUY", "SELL"} or order.quantity <= 0:
            raise ValueError("order must have a positive quantity and BUY or SELL side")
        if bar.symbol != order.symbol or bar.open is None or bar.low is None or bar.high is None or bar.is_suspended:
            return Fill("UNFILLED", side, order.symbol, 0, None, reason="MISSING_OPEN_OR_SUSPENDED", unfilled_quantity=order.quantity)
        if (side == "BUY" and bar.limit_up) or (side == "SELL" and bar.limit_down):
            return Fill("UNFILLED", side, order.symbol, 0, None, reason="PRICE_LIMIT", unfilled_quantity=order.quantity)
        if bar.available_quantity is not None and bar.available_quantity < 0:
            raise ValueError("available quantity cannot be negative")
        adjustment = Decimal("1.002") if side == "BUY" else Decimal("0.998")
        adjusted_price = bar.open * adjustment
        if adjusted_price < bar.low or adjusted_price > bar.high:
            return Fill("UNFILLED", side, order.symbol, 0, None, reason="OUT_OF_RANGE", unfilled_quantity=order.quantity)
        available = order.quantity if bar.available_quantity is None else min(order.quantity, bar.available_quantity)
        if available < order.quantity and order.fill_mode.upper() != "PARTIAL":
            return Fill("UNFILLED", side, order.symbol, 0, None, reason="FULL_OR_NONE", unfilled_quantity=order.quantity)
        status = "FILLED" if available == order.quantity else "PARTIALLY_FILLED"
        return Fill(status, side, order.symbol, available, adjusted_price, self.cost_model.calculate(side=side, quantity=available, price=adjusted_price), unfilled_quantity=order.quantity - available)

    simulate = execute
