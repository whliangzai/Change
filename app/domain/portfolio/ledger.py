"""Transactional cash and position ledger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LedgerPosition:
    symbol: str
    quantity: int
    cost_basis: Decimal
    available_quantity: int = 0


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    cash: Decimal
    positions: tuple[LedgerPosition, ...]
    equity: Decimal = Decimal("0")
    high_water_mark: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")


class PortfolioLedger:
    def __init__(self, *, initial_cash: Decimal) -> None:
        if initial_cash < 0:
            raise ValueError("initial cash cannot be negative")
        self.cash = initial_cash
        self._positions: dict[str, LedgerPosition] = {}
        self.entries: list[LedgerSnapshot] = []
        self._high_water_mark = initial_cash

    def position(self, symbol: str) -> LedgerPosition:
        return self._positions.get(symbol, LedgerPosition(symbol, 0, Decimal("0"), 0))

    @property
    def positions(self) -> tuple[LedgerPosition, ...]:
        return tuple(self._positions.values())

    def apply_fill(
        self, side: str, symbol: str, quantity: int, price: Decimal, costs: Decimal
    ) -> None:
        if quantity <= 0 or price <= 0 or costs < 0:
            raise ValueError("invalid fill")
        current = self.position(symbol)
        if side.upper() == "BUY":
            total = price * Decimal(quantity) + costs
            if total > self.cash:
                raise ValueError("insufficient cash")
            next_position = LedgerPosition(
                symbol,
                current.quantity + quantity,
                current.cost_basis + total,
                current.available_quantity,
            )
            next_cash = self.cash - total
        elif side.upper() == "SELL":
            if quantity > current.available_quantity:
                raise ValueError("insufficient shares")
            proceeds = price * Decimal(quantity) - costs
            next_cash = self.cash + proceeds
            if next_cash < 0:
                raise ValueError("sale would make cash negative")
            next_position = LedgerPosition(
                symbol,
                current.quantity - quantity,
                max(
                    Decimal("0"),
                    current.cost_basis
                    - current.cost_basis * Decimal(quantity) / Decimal(current.quantity),
                ),
                current.available_quantity - quantity,
            )
        else:
            raise ValueError("side must be BUY or SELL")
        self.cash = next_cash
        if next_position.quantity == 0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = next_position
        self.entries.append(self.snapshot())

    def snapshot(self, *, prices: Mapping[str, Decimal] | None = None) -> LedgerSnapshot:
        position_value = Decimal("0")
        for position in self._positions.values():
            mark = (prices or {}).get(
                position.symbol, position.cost_basis / Decimal(position.quantity)
            )
            position_value += mark * Decimal(position.quantity)
        equity = self.cash + position_value
        self._high_water_mark = max(self._high_water_mark, equity)
        drawdown = (
            (self._high_water_mark - equity) / self._high_water_mark
            if self._high_water_mark
            else Decimal("0")
        )
        return LedgerSnapshot(
            self.cash,
            tuple(sorted(self._positions.values(), key=lambda p: p.symbol)),
            equity,
            self._high_water_mark,
            drawdown,
        )

    def settle_all(self) -> None:
        """Make prior-day buys sellable; same-day buys remain unavailable until next call."""
        self._positions = {
            symbol: LedgerPosition(
                position.symbol, position.quantity, position.cost_basis, position.quantity
            )
            for symbol, position in self._positions.items()
        }
