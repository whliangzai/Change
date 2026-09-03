from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.backtest.metrics import calculate_metrics
from app.domain.backtest.runner import BacktestConfig, BacktestRunner
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import ExecutionSimulator, MarketBar, Order
from app.domain.portfolio.ledger import PortfolioLedger
from app.domain.risk.allocation import AllocationLimits, allocate_quantity
from app.domain.risk.drawdown import DrawdownMonitor, DrawdownState
from app.domain.strategy.features import FeatureSnapshot
from app.domain.strategy.strong_trend import StrongTrendConfig, StrongTrendStrategy


def _feature(**overrides: object) -> FeatureSnapshot:
    values: dict[str, object] = {
        "symbol": "AAA",
        "trade_date": date(2026, 1, 20),
        "close": Decimal("110"),
        "ma10": Decimal("100"),
        "market_close": Decimal("110"),
        "market_ma20": Decimal("100"),
        "return_5d": Decimal("0.05"),
        "return_20d": Decimal("0.10"),
        "amount_ratio": Decimal("1.50"),
        "reliably_buyable": True,
        "available_at": datetime(2026, 1, 20, 16, tzinfo=UTC),
        "industry": "tech",
    }
    values.update(overrides)
    return FeatureSnapshot(**values)


def test_strong_trend_uses_inclusive_boundaries_and_deterministic_ties() -> None:
    strategy = StrongTrendStrategy(StrongTrendConfig(max_positions=2))
    candidates = strategy.select_candidates(
        [
            _feature(symbol="BBB", return_5d=Decimal("0.02"), amount_ratio=Decimal("1.2")),
            _feature(symbol="AAA", return_5d=Decimal("0.02"), amount_ratio=Decimal("1.2")),
            _feature(symbol="CCC", return_5d=Decimal("0.12")),
        ],
        information_cutoff_at=datetime(2026, 1, 20, 17, tzinfo=UTC),
    )
    assert [signal.symbol for signal in candidates] == ["CCC", "AAA"]


def test_strong_trend_rejects_missing_future_and_unreliable_features() -> None:
    strategy = StrongTrendStrategy()
    assert strategy.select_candidates([_feature(return_5d=None)]) == []
    assert strategy.select_candidates([_feature(reliably_buyable=False)]) == []
    assert (
        strategy.select_candidates(
            [_feature(available_at=datetime(2026, 1, 21, tzinfo=UTC))],
            information_cutoff_at=datetime(2026, 1, 20, 17, tzinfo=UTC),
        )
        == []
    )
    assert strategy.select_candidates([_feature(is_limit_up=True)]) == []


def test_sell_signal_is_scheduled_for_next_trading_day() -> None:
    strategy = StrongTrendStrategy()
    signal = strategy.exit_signal(
        _feature(symbol="AAA"),
        entry_price=Decimal("100"),
        held_trading_days=3,
        rank=10,
        candidate_count=10,
        market_closed_streak=0,
        next_trade_date=date(2026, 1, 21),
    )
    assert signal is not None
    assert signal.reason == "MAX_HOLDING_DAYS"
    assert signal.plan_date == date(2026, 1, 21)


def test_rank_exit_is_triggered_after_the_top_half() -> None:
    signal = StrongTrendStrategy().exit_signal(
        _feature(),
        entry_price=Decimal("100"),
        held_trading_days=1,
        rank=6,
        candidate_count=10,
        market_closed_streak=0,
        next_trade_date=date(2026, 1, 21),
    )
    assert signal is not None
    assert signal.reason == "RANK_OUT_OF_POOL"


def test_sell_signal_covers_entry_loss_and_two_day_market_shutdown() -> None:
    strategy = StrongTrendStrategy()
    signal = strategy.exit_signal(
        _feature(close=Decimal("95")),
        entry_price=Decimal("100"),
        held_trading_days=1,
        rank=1,
        candidate_count=4,
        market_closed_streak=2,
        next_trade_date=date(2026, 1, 21),
    )
    assert signal is not None
    assert signal.reason == "ENTRY_DRAWDOWN"


def test_allocation_obeys_cash_limits_and_100_share_lots() -> None:
    limits = AllocationLimits()
    quantity = allocate_quantity(
        price=Decimal("30"),
        cash=Decimal("20000"),
        equity=Decimal("20000"),
        positions=[],
        industry="tech",
        limits=limits,
    )
    assert quantity == 100
    assert (
        allocate_quantity(
            price=Decimal("201"),
            cash=Decimal("100"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
            limits=limits,
        )
        == 0
    )


def test_allocation_caps_position_by_one_percent_risk_budget() -> None:
    assert (
        allocate_quantity(
            price=Decimal("10"),
            cash=Decimal("20000"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
            stop_distance=Decimal("1"),
        )
        == 200
    )


def test_allocation_includes_buy_fees_and_blocks_risky_account_states() -> None:
    assert (
        allocate_quantity(
            price=Decimal("30"),
            cash=Decimal("3500"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
        )
        == 100
    )
    with pytest.raises(ValueError):
        allocate_quantity(
            price=Decimal("30"),
            cash=Decimal("3500"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
            financing="MARGIN",
        )
    with pytest.raises(ValueError):
        allocate_quantity(
            price=Decimal("30"),
            cash=Decimal("3500"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
            account_type="MARGIN",
        )
    assert (
        allocate_quantity(
            price=Decimal("30"),
            cash=Decimal("3500"),
            equity=Decimal("20000"),
            positions=[],
            industry="tech",
            drawdown_state=DrawdownState.STOP_NEW,
        )
        == 0
    )


def test_review_state_cannot_downgrade_or_auto_recover() -> None:
    monitor = DrawdownMonitor()
    assert monitor.observe(Decimal("18400")) == DrawdownState.REVIEW_REQUIRED
    assert monitor.observe(Decimal("18800")) == DrawdownState.REVIEW_REQUIRED
    assert monitor.confirm_recovery("only-one") is False
    assert monitor.state == DrawdownState.REVIEW_REQUIRED


def test_drawdown_thresholds_and_manual_two_confirmation_recovery() -> None:
    monitor = DrawdownMonitor()
    assert monitor.observe(Decimal("18800")) == DrawdownState.STOP_NEW
    assert monitor.observe(Decimal("18400")) == DrawdownState.REVIEW_REQUIRED
    assert (
        monitor.confirm_recovery(
            "review-1", authorized=True, checklist_complete=True, equity=Decimal("20000")
        )
        is False
    )
    assert (
        monitor.confirm_recovery(
            "review-2", authorized=True, checklist_complete=True, equity=Decimal("20000")
        )
        is True
    )
    assert monitor.state == DrawdownState.NORMAL
    assert [event.action for event in monitor.audit_events] == [
        "DRAWDOWN_STOP_NEW",
        "DRAWDOWN_REVIEW_REQUIRED",
        "DRAWDOWN_RECOVERY_CONFIRMED",
    ]


def test_execution_costs_and_next_open_adjustment() -> None:
    model = CostModel()
    costs = model.calculate(side="BUY", quantity=100, price=Decimal("100"))
    assert costs.commission == Decimal("5.00")
    assert costs.stamp_duty == Decimal("0")
    assert costs.transfer_fee == Decimal("0.10")
    simulator = ExecutionSimulator(model)
    fill = simulator.execute(
        Order("BUY", "AAA", 100),
        MarketBar(
            "AAA", date(2026, 1, 21), Decimal("100"), Decimal("99"), Decimal("101"), Decimal("100")
        ),
    )
    assert fill.status == "FILLED"
    assert fill.price == Decimal("100.200")


def test_execution_rejects_missing_open_limits_and_supports_manual_partial() -> None:
    simulator = ExecutionSimulator(CostModel())
    bar = MarketBar(
        "AAA",
        date(2026, 1, 21),
        Decimal("0"),
        Decimal("99"),
        Decimal("101"),
        Decimal("100"),
        is_suspended=True,
    )
    assert simulator.execute(Order("BUY", "AAA", 100), bar).status == "UNFILLED"
    partial = simulator.execute(
        Order("BUY", "AAA", 100, fill_mode="PARTIAL"),
        MarketBar(
            "AAA",
            date(2026, 1, 21),
            Decimal("100"),
            Decimal("99"),
            Decimal("101"),
            Decimal("100"),
            available_quantity=40,
        ),
    )
    assert partial.status == "PARTIALLY_FILLED"
    assert partial.quantity == 40
    with pytest.raises(ValueError):
        simulator.execute(
            Order("BUY", "AAA", 100),
            MarketBar(
                "AAA",
                date(2026, 1, 21),
                Decimal("100"),
                Decimal("99"),
                Decimal("101"),
                Decimal("100"),
                available_quantity=-1,
            ),
        )


def test_ledger_is_transactional_and_never_allows_negative_cash_or_shares() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("20000"))
    ledger.apply_fill("BUY", "AAA", 100, Decimal("100"), Decimal("5.10"))
    assert ledger.cash == Decimal("9994.90")


def test_ledger_exposes_t_plus_one_share_availability_and_rejects_negative_sale_cash() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("20000"))
    ledger.apply_fill("BUY", "AAA", 100, Decimal("100"), Decimal("5.10"))
    with pytest.raises(ValueError):
        ledger.apply_fill("SELL", "AAA", 100, Decimal("100"), Decimal("5.10"))
    ledger.settle_all()
    ledger.apply_fill("SELL", "AAA", 100, Decimal("100"), Decimal("5.10"))
    low_cash = PortfolioLedger(initial_cash=Decimal("10"))
    low_cash.apply_fill("BUY", "PENNY", 100, Decimal("0.01"), Decimal("5.01"))
    low_cash.settle_all()
    with pytest.raises(ValueError):
        low_cash.apply_fill("SELL", "PENNY", 100, Decimal("0.01"), Decimal("5.01"))


def test_ledger_snapshot_tracks_marked_equity_high_water_and_drawdown() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("20000"))
    ledger.apply_fill("BUY", "AAA", 100, Decimal("100"), Decimal("5.10"))
    snapshot = ledger.snapshot(prices={"AAA": Decimal("90")})
    assert snapshot.equity == Decimal("18994.90")
    assert snapshot.high_water_mark == Decimal("20000")
    assert snapshot.drawdown == Decimal("0.050255")
    with pytest.raises(ValueError):
        ledger.apply_fill("SELL", "AAA", 200, Decimal("100"), Decimal("5.10"))
    assert ledger.position("AAA").quantity == 100
    assert ledger.cash == Decimal("9994.90")


def test_metrics_mark_insufficient_data_unavailable() -> None:
    result = calculate_metrics([Decimal("100"), Decimal("101")])
    assert result.available is False
    assert result.reason == "INSUFFICIENT_DATA"


def test_backtest_is_reproducible_and_binds_snapshot_hash() -> None:
    bars = [
        MarketBar(
            "AAA", date(2026, 1, 20), Decimal("100"), Decimal("99"), Decimal("101"), Decimal("100")
        ),
        MarketBar(
            "AAA", date(2026, 1, 21), Decimal("101"), Decimal("100"), Decimal("102"), Decimal("101")
        ),
    ]
    config = BacktestConfig(
        start=date(2026, 1, 20), end=date(2026, 1, 21), initial_equity=Decimal("20000")
    )
    first = BacktestRunner().run(config, bars, data_version="data-1", strategy_version="strategy-1")
    second = BacktestRunner().run(
        config, bars, data_version="data-1", strategy_version="strategy-1"
    )
    assert first.snapshot_hash == second.snapshot_hash
    assert first.snapshot.data_version == "data-1"
