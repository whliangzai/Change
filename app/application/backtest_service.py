"""Application orchestration for reproducible, research-only backtests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.errors import DataUnavailableError
from app.domain.backtest.runner import BacktestConfig, BacktestResult, BacktestRunner
from app.domain.execution.simulator import MarketBar, Order


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    config: BacktestConfig
    data_batch_id: str
    data_version: str
    strategy_version: str
    information_cutoff_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestDataSlice:
    available: bool
    reason: str | None
    data_version: str
    information_cutoff_at: datetime
    bars: tuple[MarketBar, ...]


@dataclass(frozen=True, slots=True)
class BacktestApplicationResult:
    status: str
    result: BacktestResult
    stages: tuple[str, ...]


DataLoader = Callable[[BacktestRequest], BacktestDataSlice]
OrderLoader = Callable[[BacktestRequest, Sequence[MarketBar]], Mapping[Any, Sequence[Order]]]


class BacktestApplicationService:
    """Run the domain engine only after its versioned data gate is satisfied."""

    def __init__(
        self,
        *,
        data_loader: DataLoader,
        order_loader: OrderLoader | None = None,
        runner: Any | None = None,
    ) -> None:
        self.data_loader = data_loader
        self.order_loader = order_loader
        self.runner = runner or BacktestRunner()

    def execute(self, request: BacktestRequest) -> BacktestApplicationResult:
        data = self.data_loader(request)
        if not data.available:
            raise DataUnavailableError(
                data.reason or "backtest data is unavailable",
                [{"field": "data_batch_id", "value": request.data_batch_id}],
            )
        cutoff = _as_utc(request.information_cutoff_at)
        if _as_utc(data.information_cutoff_at) > cutoff:
            raise DataUnavailableError(
                "backtest data information cutoff is later than the requested cutoff",
                [{"field": "information_cutoff_at", "value": request.information_cutoff_at}],
            )
        causal_bars = tuple(
            bar
            for bar in data.bars
            if bar.trade_date <= request.config.end
            and bar.available_at is not None
            and _as_utc(bar.available_at) <= cutoff
        )
        bars = tuple(
            bar
            for bar in causal_bars
            if request.config.start <= bar.trade_date <= request.config.end
        )
        if not bars:
            raise DataUnavailableError(
                "backtest data contains no bars available before the information cutoff",
                [{"field": "data_batch_id", "value": request.data_batch_id}],
            )
        orders = self.order_loader(request, causal_bars) if self.order_loader else None
        result = self.runner.run(
            request.config,
            bars,
            data_version=data.data_version,
            strategy_version=request.strategy_version,
            orders=orders,
        )
        return BacktestApplicationResult(
            status="SUCCEEDED",
            result=result,
            stages=("data_quality", "replay", "metrics"),
        )


def _as_utc(value: datetime) -> datetime:
    """Compare persisted SQLite timestamps and aware timestamps consistently."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "BacktestApplicationResult",
    "BacktestApplicationService",
    "BacktestDataSlice",
    "BacktestRequest",
]
