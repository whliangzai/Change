import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.backtest.runner import BacktestConfig, BacktestRunner
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import ExecutionSimulator, MarketBar, Order
from app.domain.strategy.features import DailyBar, build_feature_snapshot
from app.domain.strategy.strong_trend import StrongTrendStrategy


def _history(symbol: str, *, status: dict[str, bool] | None = None) -> list[DailyBar]:
    flags = status or {}
    return [
        DailyBar(
            symbol,
            date(2026, 1, 1) + timedelta(days=index),
            Decimal("100") + Decimal(index),
            Decimal("1000000"),
            datetime(2026, 1, 22, 16, tzinfo=UTC),
            **flags,
        )
        for index in range(21)
    ]


def test_historical_status_is_applied_and_future_injection_cannot_change_t_signal() -> None:
    market = _history("INDEX")
    stock = [
        DailyBar(
            bar.symbol, bar.trade_date, bar.close, bar.amount, datetime(2026, 1, 21, 16, tzinfo=UTC)
        )
        for bar in _history("AAA")
    ]
    feature_t = build_feature_snapshot(stock, market, symbol="AAA", trade_date=date(2026, 1, 21))
    future = stock + [DailyBar("AAA", date(2026, 1, 22), Decimal("999"), Decimal("1"))]
    feature_t_after_future = build_feature_snapshot(
        future, market, symbol="AAA", trade_date=date(2026, 1, 21)
    )
    assert feature_t == feature_t_after_future
    assert StrongTrendStrategy().select_candidates([feature_t]) == []  # data was not available at T
    assert StrongTrendStrategy().select_candidates([feature_t_after_future]) == []
    historical_bad = build_feature_snapshot(
        _history("DELISTED", status={"is_delisted": True}),
        market,
        symbol="DELISTED",
        trade_date=date(2026, 1, 21),
    )
    assert StrongTrendStrategy().select_candidates([historical_bad]) == []
    assert (
        StrongTrendStrategy().select_candidates(
            [
                build_feature_snapshot(
                    _history("STOCK_ST", status={"is_st": True}),
                    market,
                    symbol="STOCK_ST",
                    trade_date=date(2026, 1, 21),
                )
            ]
        )
        == []
    )


def test_feature_builder_filters_late_available_bars_at_information_cutoff() -> None:
    market = [
        DailyBar(
            bar.symbol, bar.trade_date, bar.close, bar.amount, datetime(2026, 1, 21, 16, tzinfo=UTC)
        )
        for bar in _history("INDEX")
    ]
    stock = [
        DailyBar(
            bar.symbol, bar.trade_date, bar.close, bar.amount, datetime(2026, 1, 21, 16, tzinfo=UTC)
        )
        for bar in _history("AAA")
    ]
    cutoff = datetime(2026, 1, 21, 17, tzinfo=UTC)
    snapshot = build_feature_snapshot(
        stock,
        market,
        symbol="AAA",
        trade_date=date(2026, 1, 21),
        information_cutoff_at=cutoff,
    )
    late_stock = [
        DailyBar(
            bar.symbol, bar.trade_date, bar.close, bar.amount, datetime(2026, 1, 22, tzinfo=UTC)
        )
        if bar.trade_date == date(2026, 1, 21)
        else bar
        for bar in stock
    ]
    late_snapshot = build_feature_snapshot(
        late_stock,
        market,
        symbol="AAA",
        trade_date=date(2026, 1, 21),
        information_cutoff_at=cutoff,
    )
    assert snapshot.available_at == cutoff.replace(hour=16)
    assert late_snapshot.close is None
    assert (
        StrongTrendStrategy().select_candidates(
            [
                build_feature_snapshot(
                    _history("HALTED", status={"is_suspended": True}),
                    market,
                    symbol="HALTED",
                    trade_date=date(2026, 1, 21),
                )
            ]
        )
        == []
    )


def test_missing_price_gap_and_limits_have_explicit_non_fill_reasons() -> None:
    simulator = ExecutionSimulator(CostModel())
    missing_open = simulator.execute(
        Order("BUY", "AAA", 100),
        MarketBar("AAA", date(2026, 1, 21), None, Decimal("99"), Decimal("101"), Decimal("100")),
    )
    assert missing_open.reason == "MISSING_OPEN_OR_SUSPENDED"
    assert missing_open.unfilled_quantity == 100
    assert (
        simulator.execute(
            Order("BUY", "AAA", 100),
            MarketBar(
                "AAA",
                date(2026, 1, 21),
                Decimal("100"),
                Decimal("100.50"),
                Decimal("101"),
                Decimal("100"),
            ),
        ).reason
        == "OUT_OF_RANGE"
    )
    assert (
        simulator.execute(
            Order("SELL", "AAA", 100),
            MarketBar(
                "AAA",
                date(2026, 1, 21),
                Decimal("100"),
                Decimal("99"),
                Decimal("101"),
                Decimal("100"),
                limit_down=True,
            ),
        ).reason
        == "PRICE_LIMIT"
    )


def test_backtest_replays_next_open_order_and_golden_snapshot() -> None:
    bars = [
        MarketBar(
            "AAA", date(2026, 1, 20), Decimal("100"), Decimal("99"), Decimal("101"), Decimal("100")
        ),
        MarketBar(
            "AAA", date(2026, 1, 21), Decimal("101"), Decimal("100"), Decimal("102"), Decimal("101")
        ),
        MarketBar(
            "AAA", date(2026, 1, 22), Decimal("102"), Decimal("101"), Decimal("103"), Decimal("102")
        ),
    ]
    result = BacktestRunner().run(
        BacktestConfig(date(2026, 1, 20), date(2026, 1, 22)),
        bars,
        data_version="fixture-data-v1",
        strategy_version="strong-trend-v1",
        orders={date(2026, 1, 21): [Order("BUY", "AAA", 100, signal_date=date(2026, 1, 20))]},
    )
    golden = json.loads(
        (
            __import__("pathlib").Path(__file__).parents[1] / "fixtures" / "golden_backtest.json"
        ).read_text()
    )
    assert result.snapshot_hash == golden["snapshot_hash"]
    assert [str(value) for value in result.equity_curve] == golden["equity_curve"]
    assert result.ledger_snapshots[-1].high_water_mark == Decimal("20074.69879800")


def test_backtest_rejects_same_day_signal_and_keeps_last_mark_when_bar_is_missing() -> None:
    bars = [
        MarketBar(
            "AAA", date(2026, 1, 20), Decimal("100"), Decimal("99"), Decimal("101"), Decimal("100")
        ),
        MarketBar(
            "AAA", date(2026, 1, 21), Decimal("101"), Decimal("100"), Decimal("102"), Decimal("101")
        ),
    ]
    result = BacktestRunner().run(
        BacktestConfig(date(2026, 1, 20), date(2026, 1, 22)),
        bars,
        data_version="fixture-data-v1",
        strategy_version="strong-trend-v1",
        orders={date(2026, 1, 21): [Order("BUY", "AAA", 100, signal_date=date(2026, 1, 21))]},
    )
    assert result.equity_curve == (Decimal("20000"), Decimal("20000"), Decimal("20000"))
    assert result.skipped_orders[0].reason == "SIGNAL_DATE_NOT_BEFORE_EXECUTION"


def test_backtest_requires_signal_provenance_for_every_order() -> None:
    bar = MarketBar(
        "AAA", date(2026, 1, 21), Decimal("100"), Decimal("99"), Decimal("101"), Decimal("100")
    )
    result = BacktestRunner().run(
        BacktestConfig(date(2026, 1, 21), date(2026, 1, 21)),
        [bar],
        data_version="fixture-data-v1",
        strategy_version="strong-trend-v1",
        orders={date(2026, 1, 21): [Order("BUY", "AAA", 100)]},
    )
    assert result.equity_curve == (Decimal("20000"), Decimal("20000"))
    assert result.skipped_orders[0].reason == "MISSING_SIGNAL_DATE"


def test_backtest_forces_full_or_none_even_when_manual_mode_requests_partial() -> None:
    bar = MarketBar(
        "AAA",
        date(2026, 1, 21),
        Decimal("100"),
        Decimal("99"),
        Decimal("101"),
        Decimal("100"),
        available_quantity=40,
    )
    result = BacktestRunner().run(
        BacktestConfig(date(2026, 1, 21), date(2026, 1, 21), fill_mode="FULL_OR_NONE"),
        [bar],
        data_version="fixture-data-v1",
        strategy_version="strong-trend-v1",
        orders={
            date(2026, 1, 21): [
                Order("BUY", "AAA", 100, fill_mode="PARTIAL", signal_date=date(2026, 1, 20))
            ]
        },
    )
    assert result.equity_curve == (Decimal("20000"), Decimal("20000"))
