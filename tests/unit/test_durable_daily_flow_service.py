from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.daily_flow_service import (
    DailyFlowApplicationService,
    DailyFlowRequest,
)
from app.core.errors import DataUnavailableError
from app.domain.execution.simulator import MarketBar


def _bar(symbol: str, day: int, close: Decimal) -> MarketBar:
    trade_date = date(2026, 9, day)
    return MarketBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        low=close - Decimal("0.10"),
        high=close + Decimal("0.10"),
        close=close,
        amount=Decimal("100000"),
        available_at=datetime(2026, 9, day, 18, tzinfo=UTC),
    )


def _request(*, available: bool = True) -> DailyFlowRequest:
    return DailyFlowRequest(
        owner_id=uuid4(),
        data_batch_id=uuid4(),
        strategy_version_id=uuid4(),
        data_version="data-v1",
        strategy_version="trend:v1",
        as_of_date=date(2026, 9, 25),
        information_cutoff_at=datetime(2026, 9, 25, 18, tzinfo=UTC),
        data_available=available,
        data_unavailable_reason=None if available else "quality gate blocked",
    )


def test_daily_flow_service_runs_causal_signal_risk_plan_and_report() -> None:
    bars = tuple(
        bar
        for day in range(1, 27)
        for bar in (
            _bar("600000.SH", day, Decimal("10") + Decimal(day) / 10),
            _bar("000300.SH", day, Decimal("100") + Decimal(day)),
        )
    )

    result = DailyFlowApplicationService().execute(
        _request(),
        bars=bars,
        strategy_parameters={"min_amount_ratio": "0"},
    )

    assert result.status == "SUCCEEDED"
    assert result.historical_pool_symbols == ("600000.SH",)
    assert result.signal_symbols == ("600000.SH",)
    assert result.plan_execution_dates == (date(2026, 9, 26),)
    assert result.plans[0].execution_date > result.as_of_date
    assert result.ledger.cash == Decimal("20000.00")
    assert result.ledger.positions == ()
    assert result.report["actual_execution_input"] is False
    assert result.report["broker_submission"] is False
    assert result.export_content_hash


def test_daily_flow_service_blocks_unavailable_data_before_signal_generation() -> None:
    with pytest.raises(DataUnavailableError, match="quality gate blocked"):
        DailyFlowApplicationService().execute(
            _request(available=False),
            bars=(),
            strategy_parameters={},
        )
