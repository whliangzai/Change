"""Decimal-only cash, position, and concentration allocation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.domain.execution.costs import CostModel
from app.domain.risk.drawdown import DrawdownState


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: int
    market_value: Decimal
    industry: str | None = None


@dataclass(frozen=True, slots=True)
class AllocationLimits:
    initial_equity: Decimal = Decimal("20000")
    max_investment_ratio: Decimal = Decimal("0.70")
    max_positions: int = 4
    target_position_value: Decimal = Decimal("3500")
    max_position_ratio: Decimal = Decimal("0.20")
    max_industry_ratio: Decimal = Decimal("0.35")
    per_trade_risk_ratio: Decimal = Decimal("0.01")
    lot_size: int = 100


def allocate_quantity(
    *,
    price: Decimal,
    cash: Decimal,
    equity: Decimal,
    positions: Sequence[Position],
    industry: str | None,
    limits: AllocationLimits | None = None,
    stop_distance: Decimal | None = None,
    symbol: str | None = None,
    cost_model: CostModel | None = None,
    drawdown_state: DrawdownState | str = DrawdownState.NORMAL,
    financing: str = "CASH",
    leverage: Decimal = Decimal("1"),
    allow_short: bool = False,
    account_type: str = "CASH",
) -> int:
    selected_limits = limits or AllocationLimits()
    if (
        account_type.upper() != "CASH"
        or financing.upper() != "CASH"
        or leverage != Decimal("1")
        or allow_short
    ):
        raise ValueError("only unleveraged cash long allocation is supported")
    if DrawdownState(drawdown_state) != DrawdownState.NORMAL:
        return 0
    current_symbol = next((position for position in positions if position.symbol == symbol), None)
    position_count = len(
        [position for position in positions if position.quantity > 0 and position.symbol != symbol]
    )
    if current_symbol is None:
        position_count += 1
    if price <= 0 or cash <= 0 or equity <= 0 or position_count > selected_limits.max_positions:
        return 0
    invested = sum((position.market_value for position in positions), Decimal("0"))
    industry_value = sum(
        (position.market_value for position in positions if position.industry == industry),
        Decimal("0"),
    )
    remaining_investment = max(
        Decimal("0"), equity * selected_limits.max_investment_ratio - invested
    )
    current_value = current_symbol.market_value if current_symbol is not None else Decimal("0")
    remaining_position = max(
        Decimal("0"), equity * selected_limits.max_position_ratio - current_value
    )
    remaining_industry = max(
        Decimal("0"), equity * selected_limits.max_industry_ratio - industry_value
    )
    notional = min(
        selected_limits.target_position_value,
        remaining_position,
        remaining_industry,
        remaining_investment,
        cash,
    )
    if stop_distance is not None:
        if stop_distance <= 0:
            return 0
        risk_quantity = equity * selected_limits.per_trade_risk_ratio / stop_distance
        notional = min(notional, risk_quantity * price)
    raw_lots = (notional / price).to_integral_value(rounding=ROUND_DOWN)
    quantity = int(raw_lots // selected_limits.lot_size * selected_limits.lot_size)
    selected_cost_model = cost_model or CostModel()
    while quantity > 0:
        total = (
            price * Decimal(quantity)
            + selected_cost_model.calculate(side="BUY", quantity=quantity, price=price).total
        )
        if total <= cash:
            break
        quantity -= selected_limits.lot_size
    return quantity


PositionLimits = AllocationLimits
