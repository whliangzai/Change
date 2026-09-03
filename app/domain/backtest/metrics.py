"""Small, explicit performance metrics with availability flags."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt


@dataclass(frozen=True, slots=True)
class Metrics:
    available: bool
    reason: str | None
    total_return: Decimal | None = None
    max_drawdown: Decimal | None = None
    sharpe: Decimal | None = None


def calculate_metrics(equity_curve: Sequence[Decimal]) -> Metrics:
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
    return Metrics(True, None, total_return, max_drawdown, sharpe)
