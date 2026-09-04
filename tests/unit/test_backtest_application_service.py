from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.application.backtest_service import (
    BacktestApplicationService,
    BacktestDataSlice,
    BacktestRequest,
)
from app.core.errors import DataUnavailableError
from app.domain.backtest.runner import BacktestConfig
from app.domain.execution.simulator import MarketBar


def _request() -> BacktestRequest:
    return BacktestRequest(
        config=BacktestConfig(
            start=date(2026, 9, 1),
            end=date(2026, 9, 3),
            training_end=date(2026, 9, 1),
            validation_end=date(2026, 9, 2),
        ),
        data_batch_id="batch-1",
        data_version="data-v1",
        strategy_version="strategy-v1",
        information_cutoff_at=datetime(2026, 9, 3, 16, 0),
    )


def _bars() -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            symbol="600000.SH",
            trade_date=trade_date,
            open=Decimal("10.00"),
            low=Decimal("9.90"),
            high=Decimal("10.20"),
            close=Decimal(str(10 + index / 10)),
            available_at=datetime(2026, 9, 3, 16, 0, tzinfo=UTC),
        )
        for index, trade_date in enumerate((date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)))
    )


def test_unavailable_data_blocks_engine_execution() -> None:
    called = False

    def loader(_: BacktestRequest) -> BacktestDataSlice:
        return BacktestDataSlice(
            available=False,
            reason="quality status is UNAVAILABLE",
            data_version="data-v1",
            information_cutoff_at=datetime(2026, 9, 3, 16, 0),
            bars=(),
        )

    def runner(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    service = BacktestApplicationService(data_loader=loader, runner=runner)

    with pytest.raises(DataUnavailableError, match="quality status is UNAVAILABLE"):
        service.execute(_request())

    assert called is False


def test_service_runs_reproducibly_and_excludes_out_of_range_bars() -> None:
    in_range = _bars()
    future = MarketBar(
        symbol="600000.SH",
        trade_date=date(2026, 9, 4),
        open=Decimal("100.00"),
        low=Decimal("99.00"),
        high=Decimal("101.00"),
        close=Decimal("100.00"),
    )

    def loader(_: BacktestRequest) -> BacktestDataSlice:
        return BacktestDataSlice(
            available=True,
            reason=None,
            data_version="data-v1",
            information_cutoff_at=datetime(2026, 9, 3, 16, 0),
            bars=in_range + (future,),
        )

    result = BacktestApplicationService(data_loader=loader).execute(_request())

    assert result.status == "SUCCEEDED"
    assert result.result.snapshot.end == date(2026, 9, 3)
    assert result.result.snapshot_hash
    assert result.result.equity_curve == (
        Decimal("20000"),
        Decimal("20000"),
        Decimal("20000"),
        Decimal("20000"),
    )
    assert result.result.metrics.available is True
    assert result.stages == ("data_quality", "replay", "metrics")


def test_service_passes_only_causally_available_bars_to_replay_and_strategy() -> None:
    cutoff = datetime(2026, 9, 3, 16, 0, tzinfo=UTC)
    available_bars = tuple(
        MarketBar(
            symbol="600000.SH",
            trade_date=trade_date,
            open=Decimal("10.00"),
            low=Decimal("9.90"),
            high=Decimal("10.20"),
            close=Decimal("10.00"),
            available_at=cutoff,
        )
        for trade_date in (date(2026, 9, 1), date(2026, 9, 2))
    )
    late_bar = MarketBar(
        symbol="600000.SH",
        trade_date=date(2026, 9, 3),
        open=Decimal("100.00"),
        low=Decimal("99.00"),
        high=Decimal("101.00"),
        close=Decimal("100.00"),
        available_at=datetime(2026, 9, 3, 17, 0, tzinfo=UTC),
    )
    observed: list[tuple[date, ...]] = []

    def loader(_: BacktestRequest) -> BacktestDataSlice:
        return BacktestDataSlice(
            available=True,
            reason=None,
            data_version="data-v1",
            information_cutoff_at=cutoff,
            bars=available_bars + (late_bar,),
        )

    def order_loader(_: BacktestRequest, bars: tuple[MarketBar, ...]) -> dict[date, list[object]]:
        observed.append(tuple(bar.trade_date for bar in bars))
        return {}

    request = BacktestRequest(
        config=BacktestConfig(
            start=date(2026, 9, 1),
            end=date(2026, 9, 3),
            training_end=date(2026, 9, 1),
            validation_end=date(2026, 9, 2),
        ),
        data_batch_id="batch-1",
        data_version="data-v1",
        strategy_version="strategy-v1",
        information_cutoff_at=cutoff,
    )

    result = BacktestApplicationService(data_loader=loader, order_loader=order_loader).execute(
        request
    )

    assert result.result.snapshot.end == date(2026, 9, 3)
    assert observed == [(date(2026, 9, 1), date(2026, 9, 2))]
    assert result.result.equity_curve == (Decimal("20000"), Decimal("20000"), Decimal("20000"))


def test_service_fails_closed_when_no_bar_is_available_at_cutoff() -> None:
    late = MarketBar(
        symbol="600000.SH",
        trade_date=date(2026, 9, 1),
        open=Decimal("10.00"),
        low=Decimal("9.90"),
        high=Decimal("10.20"),
        close=Decimal("10.00"),
        available_at=datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
    )

    def loader(_: BacktestRequest) -> BacktestDataSlice:
        return BacktestDataSlice(
            available=True,
            reason=None,
            data_version="data-v1",
            information_cutoff_at=datetime(2026, 9, 1, 16, 0, tzinfo=UTC),
            bars=(late,),
        )

    with pytest.raises(DataUnavailableError, match="available before the information cutoff"):
        BacktestApplicationService(data_loader=loader).execute(_request())
