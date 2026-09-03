"""Versioned transaction cost model using Decimal arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp_duty + self.transfer_fee


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_rate: Decimal = Decimal("0.00030")
    minimum_commission: Decimal = Decimal("5")
    sell_stamp_duty_rate: Decimal = Decimal("0.00050")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    version: str = "cost-v1"

    def calculate(self, *, side: str, quantity: int, price: Decimal) -> CostBreakdown:
        if side.upper() not in {"BUY", "SELL"} or quantity < 0 or price < 0:
            raise ValueError("quantity and price cannot be negative")
        notional = price * Decimal(quantity)
        commission = max(notional * self.commission_rate, self.minimum_commission)
        stamp = notional * self.sell_stamp_duty_rate if side.upper() == "SELL" else Decimal("0")
        transfer = notional * self.transfer_fee_rate
        return CostBreakdown(commission, stamp, transfer)

    calculate_costs = calculate


calculate_costs = CostModel().calculate
