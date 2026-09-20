"""Durable-ready daily research orchestration without broker connectivity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.errors import DataUnavailableError
from app.domain.causality import available_at_or_before
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import MarketBar
from app.domain.strategy.features import DailyBar, FeatureSnapshot, build_feature_snapshot
from app.domain.strategy.strong_trend import StrongTrendConfig, StrongTrendStrategy


@dataclass(frozen=True, slots=True)
class DailyFlowRequest:
    owner_id: UUID
    data_batch_id: UUID
    strategy_version_id: UUID
    data_version: str
    strategy_version: str
    as_of_date: date
    information_cutoff_at: datetime
    data_available: bool = True
    data_unavailable_reason: str | None = None
    benchmark_symbol: str = "000300.SH"
    initial_equity: Decimal = Decimal("20000.00")
    max_investment_ratio: Decimal = Decimal("0.70")


@dataclass(frozen=True, slots=True)
class DailyPlanCandidate:
    symbol: str
    signal_date: date
    execution_date: date
    side: str
    quantity: int
    reference_low: Decimal
    reference_high: Decimal
    estimated_cost: Decimal
    score: Decimal
    rank: int
    industry: str | None
    risk_state: str


@dataclass(frozen=True, slots=True)
class DailyLedgerState:
    cash: Decimal
    positions: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class DailyFlowResult:
    status: str
    data_version: str
    strategy_version: str
    as_of_date: date
    future_rows: int
    survivor_bias_detected: bool
    historical_pool_symbols: tuple[str, ...]
    signal_symbols: tuple[str, ...]
    risk_rejected_symbols: tuple[str, ...]
    plans: tuple[DailyPlanCandidate, ...]
    ledger: DailyLedgerState
    report: dict[str, Any]
    export_content_hash: str
    completed_stages: tuple[str, ...]

    @property
    def plan_execution_dates(self) -> tuple[date, ...]:
        return tuple(sorted({plan.execution_date for plan in self.plans}))


class DailyFlowApplicationService:
    """Run the import-to-report stages from supplied, versioned market data.

    The service only creates research plans. Manual confirmation and actual fills
    remain separate API operations; no broker client is accepted by this boundary.
    """

    def __init__(self, cost_model: CostModel | None = None) -> None:
        self.cost_model = cost_model or CostModel()

    def execute(
        self,
        request: DailyFlowRequest,
        *,
        bars: tuple[MarketBar, ...] | list[MarketBar],
        strategy_parameters: dict[str, object],
    ) -> DailyFlowResult:
        if request.information_cutoff_at.tzinfo is None:
            request = replace(
                request,
                information_cutoff_at=request.information_cutoff_at.replace(tzinfo=UTC),
            )
        if not request.data_available:
            raise DataUnavailableError(
                request.data_unavailable_reason or "daily data is unavailable",
                [{"field": "data_batch_id", "value": str(request.data_batch_id)}],
            )

        ordered = tuple(sorted(bars, key=lambda bar: (bar.trade_date, bar.symbol)))
        future_rows = sum(
            1
            for bar in ordered
            if bar.trade_date > request.as_of_date and self._visible(bar, request)
        )
        survivor_bias_detected = any(
            bar.trade_date <= request.as_of_date
            and bar.list_date is not None
            and bar.list_date > request.as_of_date
            for bar in ordered
        )
        if survivor_bias_detected:
            raise DataUnavailableError(
                "historical input contains a security before its listing date",
                [{"field": "data_batch_id", "value": str(request.data_batch_id)}],
            )

        strategy_bars = tuple(
            self._to_strategy_bar(bar) for bar in ordered if bar.close is not None
        )
        visible_as_of = tuple(
            bar
            for bar in strategy_bars
            if bar.trade_date <= request.as_of_date and self._strategy_visible(bar, request)
        )
        market_bars = tuple(bar for bar in visible_as_of if bar.symbol == request.benchmark_symbol)
        pool = tuple(
            sorted(
                {
                    bar.symbol
                    for bar in visible_as_of
                    if bar.trade_date == request.as_of_date
                    and bar.symbol != request.benchmark_symbol
                    and not bar.is_suspended
                    and not bar.is_st
                    and not bar.is_delisted
                    and (bar.list_date is None or bar.list_date <= request.as_of_date)
                }
            )
        )
        strategy = StrongTrendStrategy(self._strategy_config(strategy_parameters))
        features = tuple(
            build_feature_snapshot(
                visible_as_of,
                market_bars,
                symbol=symbol,
                trade_date=request.as_of_date,
                information_cutoff_at=request.information_cutoff_at,
            )
            for symbol in pool
        )
        signals = tuple(
            strategy.select_candidates(
                features, information_cutoff_at=request.information_cutoff_at
            )
        )
        candidate_status = "CANDIDATES_AVAILABLE" if signals else "NO_CANDIDATES"
        candidate_reason = (
            None if signals else "No securities passed the published strategy filters"
        )
        next_trade_date = next(
            (
                bar.trade_date
                for bar in ordered
                if bar.trade_date > request.as_of_date and bar.symbol == request.benchmark_symbol
            ),
            None,
        )
        plans: list[DailyPlanCandidate] = []
        rejected: list[str] = []
        committed = Decimal("0")
        if next_trade_date is not None:
            for signal in signals:
                feature = next(item for item in features if item.symbol == signal.symbol)
                reference = feature.close or Decimal("0")
                estimated = (
                    reference * Decimal("100")
                    + self.cost_model.calculate(side="BUY", quantity=100, price=reference).total
                )
                if (
                    reference <= 0
                    or committed + estimated > request.initial_equity * request.max_investment_ratio
                ):
                    rejected.append(signal.symbol)
                    continue
                committed += estimated
                plans.append(
                    DailyPlanCandidate(
                        symbol=signal.symbol,
                        signal_date=signal.trade_date,
                        execution_date=next_trade_date,
                        side="BUY",
                        quantity=100,
                        reference_low=reference,
                        reference_high=reference,
                        estimated_cost=estimated,
                        score=signal.score,
                        rank=signal.rank,
                        industry=signal.industry,
                        risk_state="NORMAL",
                    )
                )
        ledger = DailyLedgerState(cash=request.initial_equity)
        report = {
            "data_quality": "AVAILABLE",
            "data_batch_id": str(request.data_batch_id),
            "data_version": request.data_version,
            "strategy_version": request.strategy_version,
            "historical_pool": list(pool),
            "candidates": [self._feature_record(feature) for feature in features],
            "signals": [self._signal_record(signal) for signal in signals],
            "candidate_status": candidate_status,
            "candidate_reason": candidate_reason,
            "order_plans": [self._plan_record(plan) for plan in plans],
            "risk_rejected_symbols": rejected,
            "account": {"equity": str(ledger.cash), "cash": str(ledger.cash)},
            "risk_state": "NORMAL",
            "actual_execution_input": False,
            "manual_confirmation_required": bool(plans),
            "broker_submission": False,
            "notice": "研究用途；不构成投资建议；不承诺收益；不自动下单。",
        }
        export_content_hash = hashlib.sha256(
            json.dumps(report, default=str, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return DailyFlowResult(
            status="SUCCEEDED",
            data_version=request.data_version,
            strategy_version=request.strategy_version,
            as_of_date=request.as_of_date,
            future_rows=future_rows,
            survivor_bias_detected=False,
            historical_pool_symbols=pool,
            signal_symbols=tuple(signal.symbol for signal in signals),
            risk_rejected_symbols=tuple(rejected),
            plans=tuple(plans),
            ledger=ledger,
            report=report,
            export_content_hash=export_content_hash,
            completed_stages=(
                "data_quality",
                "historical_pool",
                "features",
                "signals",
                "risk",
                "t_plus_one_plan",
                "ledger",
                "daily_report",
                "export",
            ),
        )

    @staticmethod
    def _visible(bar: MarketBar, request: DailyFlowRequest) -> bool:
        return available_at_or_before(bar.available_at, request.information_cutoff_at)

    @classmethod
    def _strategy_visible(cls, bar: DailyBar, request: DailyFlowRequest) -> bool:
        return cls._visible(
            MarketBar(
                symbol=bar.symbol,
                trade_date=bar.trade_date,
                open=bar.close,
                low=bar.close,
                high=bar.close,
                close=bar.close,
                available_at=bar.available_at,
            ),
            request,
        )

    @staticmethod
    def _to_strategy_bar(bar: MarketBar) -> DailyBar:
        available_at = bar.available_at
        if available_at is not None and available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        return DailyBar(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            close=bar.close or Decimal("0"),
            amount=bar.amount or Decimal("0"),
            available_at=available_at,
            is_suspended=bar.is_suspended,
            is_st=bar.is_st,
            is_delisted=bar.is_delisted,
            is_limit_up=bar.close_limit_up is not False,
            industry=bar.industry,
            list_date=bar.list_date,
        )

    @staticmethod
    def _strategy_config(parameters: dict[str, object]) -> StrongTrendConfig:
        defaults = StrongTrendConfig()
        return StrongTrendConfig(
            max_positions=int(str(parameters.get("max_positions", defaults.max_positions))),
            max_per_industry=int(
                str(parameters.get("max_per_industry", defaults.max_per_industry))
            ),
            min_return_5d=Decimal(str(parameters.get("min_return_5d", defaults.min_return_5d))),
            max_return_5d=Decimal(str(parameters.get("max_return_5d", defaults.max_return_5d))),
            min_amount_ratio=Decimal(
                str(parameters.get("min_amount_ratio", defaults.min_amount_ratio))
            ),
            max_amount_ratio=Decimal(
                str(parameters.get("max_amount_ratio", defaults.max_amount_ratio))
            ),
            max_holding_days=int(
                str(parameters.get("max_holding_days", defaults.max_holding_days))
            ),
            drawdown_exit=Decimal(str(parameters.get("drawdown_exit", defaults.drawdown_exit))),
        )

    @staticmethod
    def _feature_record(feature: FeatureSnapshot) -> dict[str, Any]:
        return {
            "symbol": feature.symbol,
            "trade_date": feature.trade_date.isoformat(),
            "close": str(feature.close) if feature.close is not None else None,
            "return_5d": str(feature.return_5d) if feature.return_5d is not None else None,
            "return_20d": str(feature.return_20d) if feature.return_20d is not None else None,
            "amount_ratio": str(feature.amount_ratio) if feature.amount_ratio is not None else None,
            "reliably_buyable": feature.reliably_buyable,
        }

    @staticmethod
    def _signal_record(signal: Any) -> dict[str, Any]:
        return {
            "symbol": signal.symbol,
            "trade_date": signal.trade_date.isoformat(),
            "score": str(signal.score),
            "rank": signal.rank,
            "industry": signal.industry,
        }

    @staticmethod
    def _plan_record(plan: DailyPlanCandidate) -> dict[str, Any]:
        record = asdict(plan)
        record["signal_date"] = plan.signal_date.isoformat()
        record["execution_date"] = plan.execution_date.isoformat()
        for key in ("reference_low", "reference_high", "estimated_cost", "score"):
            record[key] = str(record[key])
        return record


__all__ = ["DailyFlowApplicationService", "DailyFlowRequest", "DailyFlowResult"]
