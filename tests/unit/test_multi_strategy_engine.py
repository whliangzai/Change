from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.backtest.event_loop import StatefulBacktestRunner
from app.domain.backtest.runner import BacktestConfig, BacktestSeries
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import ExecutionSimulator, MarketBar, Order
from app.domain.portfolio.ledger import PortfolioLedger
from app.domain.strategy.features import DailyBar
from app.domain.strategy.ma_trend import MATrendStrategy, build_ma_feature
from app.domain.strategy.registry import expand_parameters, strategy_catalog


def _market_bar(symbol: str, trade_date: date, close: Decimal) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close - Decimal("0.02"),
        low=close - Decimal("0.10"),
        high=close + Decimal("0.10"),
        close=close,
        amount=Decimal("1000000"),
        available_at=datetime.combine(trade_date, datetime.min.time(), UTC),
        industry="TECH",
        universe_eligible=True,
    )


def _trend_bars(*, naive_available_at: bool = False) -> tuple[MarketBar, ...]:
    start = date(2026, 1, 1)
    result: list[MarketBar] = []
    for index in range(25):
        trade_date = start + timedelta(days=index)
        for symbol, close in (
            ("AAA", Decimal("10") + Decimal(index) / Decimal("10")),
            ("000300.SH", Decimal("100") + Decimal(index)),
        ):
            bar = _market_bar(symbol, trade_date, close)
            result.append(
                replace(
                    bar,
                    available_at=(
                        datetime.combine(trade_date, datetime.min.time())
                        if naive_available_at
                        else bar.available_at
                    ),
                )
            )
    return tuple(result)


def test_registry_expands_defaults_and_rejects_unknown_or_invalid_parameters() -> None:
    records = {item["strategy_type"]: item for item in strategy_catalog()}
    assert set(records) == {"STRONG_TREND", "MA_TREND"}
    assert records["MA_TREND"]["approved_for_daily"] is False
    assert expand_parameters("MA_TREND", {}) == {
        "short_window": 20,
        "long_window": 60,
        "max_positions": 4,
        "max_per_industry": 2,
        "max_holding_days": 20,
        "require_market_above_long_ma": True,
    }
    with pytest.raises(ValidationError):
        expand_parameters("MA_TREND", {"unknown": 1})
    with pytest.raises(ValueError, match="short_window"):
        expand_parameters("MA_TREND", {"short_window": 60, "long_window": 20})


def test_execution_fails_closed_when_price_limit_state_is_unproven() -> None:
    bar = _market_bar("AAA", date(2026, 1, 2), Decimal("10"))
    simulator = ExecutionSimulator()
    normal = simulator.execute(Order("BUY", "AAA", 100), bar)
    limit_up = simulator.execute(Order("BUY", "AAA", 100), replace(bar, limit_up=True))
    limit_down = simulator.execute(Order("SELL", "AAA", 100), replace(bar, limit_down=True))
    fill = simulator.execute(
        Order("BUY", "AAA", 100),
        replace(bar, limit_status_known=False),
    )
    assert normal.status == "FILLED"
    assert limit_up.reason == "PRICE_LIMIT"
    assert limit_down.reason == "PRICE_LIMIT"
    assert fill.status == "UNFILLED"
    assert fill.reason == "LIMIT_STATUS_UNKNOWN"


def test_close_limit_blocks_signal_while_open_limits_only_control_execution() -> None:
    start = date(2026, 1, 1)
    bars = tuple(
        replace(
            bar,
            limit_up=False,
            close_limit_up=(bar.symbol == "AAA" and bar.trade_date == start + timedelta(days=20)),
        )
        for bar in _trend_bars()
    )
    result = StatefulBacktestRunner().run(
        BacktestConfig(start=start + timedelta(days=20), end=start + timedelta(days=21)),
        bars,
        data_version="data-v1",
        strategy_version="trend:v1",
        strategy_type="STRONG_TREND",
        strategy_parameters={"min_amount_ratio": "0"},
        information_cutoff_at=datetime(2027, 1, 1, tzinfo=UTC),
        strategy_implementation_version="strong-trend-v2",
        trading_dates=(start + timedelta(days=20), start + timedelta(days=21)),
        secondary_benchmark=None,
        universe_benchmark_enabled=False,
    )

    assert not [fill for fill in result.fills if fill.side == "BUY"]


def test_limit_down_sell_keeps_the_existing_position() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("20000"))
    ledger.apply_fill("BUY", "AAA", 100, Decimal("10"), Decimal("5"))
    ledger.settle_all()
    bar = replace(
        _market_bar("AAA", date(2026, 1, 2), Decimal("10")),
        limit_down=True,
        close_limit_down=False,
    )
    fill = ExecutionSimulator().execute(Order("SELL", "AAA", 100), bar)
    if fill.status == "FILLED" and fill.price is not None and fill.costs is not None:
        ledger.apply_fill(fill.side, fill.symbol, fill.quantity, fill.price, fill.costs.total)

    assert fill.reason == "PRICE_LIMIT"
    assert ledger.position("AAA").quantity == 100


def test_ma_trend_requires_long_history_and_uses_symbol_as_stable_tie_break() -> None:
    start = date(2026, 1, 1)
    bars = tuple(
        DailyBar(
            symbol=symbol,
            trade_date=start + timedelta(days=index),
            close=Decimal("10") + Decimal(index) / Decimal("10"),
            amount=Decimal("100000"),
            available_at=datetime.combine(start + timedelta(days=index), datetime.min.time(), UTC),
            industry="TECH",
        )
        for index in range(60)
        for symbol in ("BBB", "AAA")
    )
    market = tuple(
        DailyBar(
            symbol="000300.SH",
            trade_date=start + timedelta(days=index),
            close=Decimal("100") + Decimal(index),
            amount=Decimal("100000"),
            available_at=datetime.combine(start + timedelta(days=index), datetime.min.time(), UTC),
        )
        for index in range(60)
    )
    early = build_ma_feature(
        bars,
        market,
        symbol="AAA",
        trade_date=start + timedelta(days=58),
        short_window=20,
        long_window=60,
    )
    assert MATrendStrategy().select_candidates([early]) == []
    features = [
        build_ma_feature(
            bars,
            market,
            symbol=symbol,
            trade_date=start + timedelta(days=59),
            short_window=20,
            long_window=60,
        )
        for symbol in ("BBB", "AAA")
    ]
    assert [item.symbol for item in MATrendStrategy().select_candidates(features)] == [
        "AAA",
        "BBB",
    ]


def test_engine_v2_sizes_positions_dynamically_exits_and_builds_all_benchmarks() -> None:
    start = date(2026, 1, 1)
    bars: list[MarketBar] = []
    for index in range(66):
        trade_date = start + timedelta(days=index)
        bars.extend(
            (
                _market_bar("AAA", trade_date, Decimal("10") + Decimal(index) / 10),
                _market_bar("000300.SH", trade_date, Decimal("100") + Decimal(index)),
                _market_bar("000001.SH", trade_date, Decimal("90") + Decimal(index) / 2),
            )
        )
    result = StatefulBacktestRunner().run(
        BacktestConfig(
            start=start + timedelta(days=59),
            end=start + timedelta(days=65),
            training_end=start + timedelta(days=61),
            validation_end=start + timedelta(days=63),
            oos_start=start + timedelta(days=64),
        ),
        tuple(bars),
        data_version="data-v1",
        strategy_version="ma:v1",
        strategy_type="MA_TREND",
        strategy_parameters={
            "short_window": 20,
            "long_window": 60,
            "max_positions": 4,
            "max_per_industry": 2,
            "max_holding_days": 2,
            "require_market_above_long_ma": True,
        },
        information_cutoff_at=datetime(2027, 1, 1, tzinfo=UTC),
        strategy_implementation_version="ma-trend-v1",
        trading_dates=tuple(start + timedelta(days=index) for index in range(59, 66)),
    )
    filled = [fill for fill in result.fills if fill.status == "FILLED"]
    assert result.engine_version == "engine-v2"
    assert any(fill.side == "BUY" and fill.quantity > 100 for fill in filled)
    assert any(fill.side == "SELL" for fill in filled)
    assert {item.series_code for item in result.series} == {
        "STRATEGY",
        "BENCHMARK_PRIMARY",
        "BENCHMARK_SECONDARY",
        "UNIVERSE_EQUAL_WEIGHT",
    }
    assert (
        next(
            item for item in result.series if item.series_code == "UNIVERSE_EQUAL_WEIGHT"
        ).availability
        == "SYNTHETIC_NOT_INVESTABLE"
    )
    assert {item.segment for item in result.segment_metrics} == {
        "FULL",
        "TRAIN",
        "VALIDATION",
        "OOS",
    }


def test_engine_normalizes_naive_bar_availability_against_aware_cutoff() -> None:
    start = date(2026, 1, 1)
    result = StatefulBacktestRunner().run(
        BacktestConfig(
            start=start + timedelta(days=20),
            end=start + timedelta(days=24),
            training_end=start + timedelta(days=21),
            validation_end=start + timedelta(days=22),
            oos_start=start + timedelta(days=23),
        ),
        _trend_bars(naive_available_at=True),
        data_version="data-v1",
        strategy_version="trend:v1",
        strategy_type="STRONG_TREND",
        strategy_parameters={},
        information_cutoff_at=datetime(2027, 1, 1, tzinfo=UTC),
        strategy_implementation_version="strong-trend-v2",
        trading_dates=tuple(start + timedelta(days=index) for index in range(20, 25)),
        secondary_benchmark=None,
        universe_benchmark_enabled=False,
    )

    assert len(result.ledger_snapshots) == 5


def test_segment_metrics_include_the_pre_segment_boundary_equity() -> None:
    start = date(2026, 1, 1)
    points = tuple(
        (start + timedelta(days=index), value)
        for index, value in enumerate(
            (Decimal("100"), Decimal("110"), Decimal("121"), Decimal("133.1"))
        )
    )
    metrics = StatefulBacktestRunner._segment_metrics(
        [BacktestSeries("STRATEGY", points)],
        BacktestConfig(
            start=start,
            end=start + timedelta(days=3),
            training_end=start,
            validation_end=start + timedelta(days=3),
            oos_start=start + timedelta(days=2),
        ),
        [],
    )
    by_segment = {item.segment: item.metrics for item in metrics}

    assert by_segment["VALIDATION"].total_return == Decimal("0.331")
    assert by_segment["OOS"].total_return == Decimal("0.21")


def test_snapshot_hash_binds_oos_and_benchmark_configuration() -> None:
    start = date(2026, 1, 1)
    bars = _trend_bars()
    config = BacktestConfig(
        start=start + timedelta(days=20),
        end=start + timedelta(days=24),
        training_end=start + timedelta(days=21),
        validation_end=start + timedelta(days=22),
        oos_start=start + timedelta(days=23),
    )
    common = {
        "data_version": "data-v1",
        "strategy_version": "trend:v1",
        "strategy_type": "STRONG_TREND",
        "strategy_parameters": {},
        "information_cutoff_at": datetime(2027, 1, 1, tzinfo=UTC),
        "strategy_implementation_version": "strong-trend-v2",
        "trading_dates": tuple(start + timedelta(days=index) for index in range(20, 25)),
        "secondary_benchmark": None,
    }
    baseline = StatefulBacktestRunner().run(
        config,
        bars,
        universe_benchmark_enabled=True,
        **common,
    )
    no_universe = StatefulBacktestRunner().run(
        config,
        bars,
        universe_benchmark_enabled=False,
        **common,
    )
    different_oos = StatefulBacktestRunner().run(
        replace(config, oos_start=start + timedelta(days=24)),
        bars,
        universe_benchmark_enabled=True,
        **common,
    )
    different_rule = StatefulBacktestRunner().run(
        config,
        bars,
        universe_benchmark_enabled=True,
        rule_version="rule-v2-tight",
        rule_config={
            "max_investment_ratio": "0.50",
            "max_positions": 4,
            "max_position_ratio": "0.20",
            "max_industry_ratio": "0.35",
            "target_position_value": "3500",
            "lot_size": 100,
            "drawdown_stop_new": "0.06",
            "drawdown_review_required": "0.08",
            "automatic_risk_recovery": False,
        },
        **common,
    )

    assert (
        len(
            {
                baseline.snapshot_hash,
                no_universe.snapshot_hash,
                different_oos.snapshot_hash,
                different_rule.snapshot_hash,
            }
        )
        == 4
    )


def test_snapshot_hash_binds_full_costs_batch_set_and_missing_bar_suspensions() -> None:
    start = date(2026, 1, 1)
    bars = _trend_bars()
    config = BacktestConfig(
        start=start + timedelta(days=20),
        end=start + timedelta(days=24),
        training_end=start + timedelta(days=21),
        validation_end=start + timedelta(days=22),
        oos_start=start + timedelta(days=23),
    )
    common = {
        "data_version": "batch-set-v1",
        "strategy_version": "trend:v1",
        "strategy_type": "STRONG_TREND",
        "strategy_parameters": {},
        "information_cutoff_at": datetime(2027, 1, 1, tzinfo=UTC),
        "strategy_implementation_version": "strong-trend-v2",
        "trading_dates": tuple(start + timedelta(days=index) for index in range(20, 25)),
        "secondary_benchmark": None,
        "universe_benchmark_enabled": False,
        "data_batches": (
            {
                "batch_id": "batch-1",
                "content_hash": "content-1",
                "start_date": "2026-01-01",
                "end_date": "2026-01-25",
                "status_history_hash": "status-1",
            },
        ),
    }
    baseline = StatefulBacktestRunner(
        CostModel(slippage_buy=Decimal("0.002"), slippage_sell=Decimal("0.002"))
    ).run(config, bars, suspended_symbols_by_date={}, **common)
    different_cost = StatefulBacktestRunner(
        CostModel(slippage_buy=Decimal("0.003"), slippage_sell=Decimal("0.002"))
    ).run(config, bars, suspended_symbols_by_date={}, **common)
    different_suspension = StatefulBacktestRunner(
        CostModel(slippage_buy=Decimal("0.002"), slippage_sell=Decimal("0.002"))
    ).run(
        config,
        bars,
        suspended_symbols_by_date={start + timedelta(days=22): frozenset({"MISSING.SH"})},
        **common,
    )
    different_batch = StatefulBacktestRunner(
        CostModel(slippage_buy=Decimal("0.002"), slippage_sell=Decimal("0.002"))
    ).run(
        config,
        bars,
        suspended_symbols_by_date={},
        **{
            **common,
            "data_batches": (
                {
                    **common["data_batches"][0],
                    "status_history_hash": "status-2",
                },
            ),
        },
    )

    assert (
        len(
            {
                baseline.snapshot_hash,
                different_cost.snapshot_hash,
                different_suspension.snapshot_hash,
                different_batch.snapshot_hash,
            }
        )
        == 4
    )


def test_max_per_industry_counts_existing_positions_across_days() -> None:
    start = date(2026, 1, 1)
    bars: list[MarketBar] = []
    for index in range(66):
        trade_date = start + timedelta(days=index)
        values = {
            "AAA": Decimal("10") + Decimal(index) / Decimal("10"),
            "BBB": Decimal("9") + Decimal(index) / Decimal("10"),
            "CCC": Decimal("8")
            + Decimal(index) / Decimal("20")
            + (Decimal("10") if index >= 60 else Decimal("0")),
            "DDD": Decimal("7")
            + Decimal(index) / Decimal("20")
            + (Decimal("10") if index >= 60 else Decimal("0")),
            "000300.SH": Decimal("100") + Decimal(index),
        }
        bars.extend(_market_bar(symbol, trade_date, close) for symbol, close in values.items())
    rule_config = {
        "max_investment_ratio": "1",
        "max_positions": 4,
        "max_position_ratio": "0.25",
        "max_industry_ratio": "1",
        "target_position_value": "3500",
        "lot_size": 100,
        "drawdown_stop_new": "0.06",
        "drawdown_review_required": "0.08",
        "automatic_risk_recovery": False,
    }
    result = StatefulBacktestRunner().run(
        BacktestConfig(
            start=start + timedelta(days=59),
            end=start + timedelta(days=65),
            training_end=start + timedelta(days=61),
            validation_end=start + timedelta(days=63),
            oos_start=start + timedelta(days=64),
        ),
        tuple(bars),
        data_version="data-v1",
        strategy_version="ma:v1",
        strategy_type="MA_TREND",
        strategy_parameters={
            "short_window": 20,
            "long_window": 60,
            "max_positions": 4,
            "max_per_industry": 2,
            "max_holding_days": 20,
            "require_market_above_long_ma": True,
        },
        information_cutoff_at=datetime(2027, 1, 1, tzinfo=UTC),
        strategy_implementation_version="ma-trend-v1",
        trading_dates=tuple(start + timedelta(days=index) for index in range(59, 66)),
        rule_config=rule_config,
        secondary_benchmark=None,
        universe_benchmark_enabled=False,
    )
    bought = {
        fill.symbol for fill in result.fills if fill.side == "BUY" and fill.status == "FILLED"
    }

    assert len(bought) == 2
