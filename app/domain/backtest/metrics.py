"""Small, explicit performance metrics with availability flags."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt

from app.domain.execution.simulator import Fill


@dataclass(frozen=True, slots=True)
class Metrics:
    available: bool
    reason: str | None
    total_return: Decimal | None = None
    max_drawdown: Decimal | None = None
    sharpe: Decimal | None = None
    win_rate: Decimal | None = None
    profit_loss_ratio: Decimal | None = None
    average_holding_period: Decimal | None = None
    turnover: Decimal | None = None
    total_cost: Decimal | None = None
    industry_exposure: tuple[tuple[str, Decimal], ...] = ()
    monthly_returns: tuple[tuple[str, Decimal], ...] = ()
    yearly_returns: tuple[tuple[str, Decimal], ...] = ()


def calculate_metrics(
    equity_curve: Sequence[Decimal],
    *,
    fills: Sequence[Fill] = (),
    trade_dates: Sequence[date] = (),
) -> Metrics:
    if len(equity_curve) < 3 or any(value <= 0 for value in equity_curve):
        return Metrics(False, "INSUFFICIENT_DATA")
    start = equity_curve[0]
    total_return = equity_curve[-1] / start - 1
    peak = equity_curve[0]
    max_drawdown = Decimal("0")
    returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
        returns.append(float(current / previous - 1))
        peak = max(peak, current)
        max_drawdown = max(max_drawdown, (peak - current) / peak)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    sharpe = Decimal(str(mean / sqrt(variance) * sqrt(252))) if variance else None
    closed_pnls: list[Decimal] = []
    holding_days: list[Decimal] = []
    lots: dict[str, list[tuple[int, Decimal, date | None]]] = {}
    total_cost = Decimal("0")
    turnover_value = Decimal("0")
    industry_notional: dict[str, Decimal] = {}
    for fill in fills:
        if fill.quantity <= 0 or fill.price is None:
            continue
        costs = fill.costs.total if fill.costs is not None else Decimal("0")
        notional = fill.price * Decimal(fill.quantity)
        total_cost += costs
        turnover_value += notional
        if fill.status not in {"FILLED", "PARTIALLY_FILLED"}:
            continue
        if fill.side.upper() == "BUY":
            lots.setdefault(fill.symbol, []).append(
                (fill.quantity, notional + costs, fill.execution_date)
            )
            industry = fill.industry or "UNKNOWN"
            industry_notional[industry] = industry_notional.get(industry, Decimal("0")) + notional
            continue
        if fill.side.upper() != "SELL":
            continue
        remaining = fill.quantity
        proceeds = notional - costs
        while remaining and lots.get(fill.symbol):
            lot_quantity, lot_cost, entry_date = lots[fill.symbol][0]
            matched = min(remaining, lot_quantity)
            allocated_cost = lot_cost * Decimal(matched) / Decimal(lot_quantity)
            allocated_proceeds = proceeds * Decimal(matched) / Decimal(fill.quantity)
            closed_pnls.append(allocated_proceeds - allocated_cost)
            if entry_date is not None and fill.execution_date is not None:
                holding_days.append(Decimal((fill.execution_date - entry_date).days))
            remaining -= matched
            if matched == lot_quantity:
                lots[fill.symbol].pop(0)
            else:
                lots[fill.symbol][0] = (
                    lot_quantity - matched,
                    lot_cost - allocated_cost,
                    entry_date,
                )
    wins = [value for value in closed_pnls if value > 0]
    losses = [-value for value in closed_pnls if value < 0]
    win_rate = Decimal(len(wins)) / Decimal(len(closed_pnls)) if closed_pnls else None
    total_wins = _decimal_sum(wins)
    total_losses = _decimal_sum(losses)
    profit_loss_ratio = (
        (total_wins / Decimal(len(wins))) / (total_losses / Decimal(len(losses)))
        if wins and losses and total_losses
        else None
    )
    exposure_total = _decimal_sum(tuple(industry_notional.values()))
    industry_exposure = (
        tuple(
            (industry, notional / exposure_total)
            for industry, notional in sorted(industry_notional.items())
        )
        if exposure_total
        else ()
    )
    return Metrics(
        True,
        None,
        total_return,
        max_drawdown,
        sharpe,
        win_rate,
        profit_loss_ratio,
        _decimal_sum(holding_days) / Decimal(len(holding_days)) if holding_days else None,
        turnover_value / equity_curve[0] if equity_curve[0] else None,
        total_cost,
        industry_exposure,
        _period_returns(equity_curve, trade_dates, monthly=True),
        _period_returns(equity_curve, trade_dates, monthly=False),
    )


def _decimal_sum(values: Sequence[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def _period_returns(
    equity_curve: Sequence[Decimal], trade_dates: Sequence[date], *, monthly: bool
) -> tuple[tuple[str, Decimal], ...]:
    if len(trade_dates) != len(equity_curve) - 1 or not trade_dates:
        return ()
    values: dict[str, Decimal] = {}
    order: list[str] = []
    for index, trade_date in enumerate(trade_dates, start=1):
        key = trade_date.strftime("%Y-%m" if monthly else "%Y")
        if key not in values:
            order.append(key)
        values[key] = equity_curve[index]
    previous = equity_curve[0]
    result: list[tuple[str, Decimal]] = []
    for key in order:
        current = values[key]
        result.append((key, current / previous - 1))
        previous = current
    return tuple(result)
