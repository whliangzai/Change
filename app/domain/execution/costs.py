"""Versioned transaction cost model using Decimal arithmetic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    regulatory_fee: Decimal = Decimal("0")
    handling_fee: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return (
            self.commission
            + self.stamp_duty
            + self.transfer_fee
            + self.regulatory_fee
            + self.handling_fee
        )


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_rate: Decimal = Decimal("0.00030")
    minimum_commission: Decimal = Decimal("5")
    sell_stamp_duty_rate: Decimal = Decimal("0.00050")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    regulatory_fee_rate: Decimal = Decimal("0")
    handling_fee_rate: Decimal = Decimal("0")
    commission_includes_regulatory: bool = True
    commission_includes_handling: bool = True
    slippage_buy: Decimal = Decimal("0.002")
    slippage_sell: Decimal = Decimal("0.002")
    version: str = "cost-v1"

    def configuration(self) -> dict[str, object]:
        return {
            "version": self.version,
            "commission_rate": str(self.commission_rate),
            "minimum_commission": str(self.minimum_commission),
            "sell_stamp_duty_rate": str(self.sell_stamp_duty_rate),
            "transfer_fee_rate": str(self.transfer_fee_rate),
            "regulatory_fee_rate": str(self.regulatory_fee_rate),
            "handling_fee_rate": str(self.handling_fee_rate),
            "commission_includes_regulatory": self.commission_includes_regulatory,
            "commission_includes_handling": self.commission_includes_handling,
            "slippage_buy": str(self.slippage_buy),
            "slippage_sell": str(self.slippage_sell),
        }

    def configuration_hash(self) -> str:
        payload = json.dumps(self.configuration(), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def execution_price(self, *, side: str, open_price: Decimal) -> Decimal:
        slippage = self.slippage_buy if side.upper() == "BUY" else self.slippage_sell
        direction = Decimal("1") if side.upper() == "BUY" else Decimal("-1")
        return open_price * (Decimal("1") + direction * slippage)

    def calculate(self, *, side: str, quantity: int, price: Decimal) -> CostBreakdown:
        if side.upper() not in {"BUY", "SELL"} or quantity < 0 or price < 0:
            raise ValueError("quantity and price cannot be negative")
        notional = price * Decimal(quantity)
        commission = max(notional * self.commission_rate, self.minimum_commission)
        stamp = notional * self.sell_stamp_duty_rate if side.upper() == "SELL" else Decimal("0")
        transfer = notional * self.transfer_fee_rate
        regulatory = (
            Decimal("0")
            if self.commission_includes_regulatory
            else notional * self.regulatory_fee_rate
        )
        handling = (
            Decimal("0") if self.commission_includes_handling else notional * self.handling_fee_rate
        )
        return CostBreakdown(commission, stamp, transfer, regulatory, handling)

    calculate_costs = calculate


calculate_costs = CostModel().calculate
