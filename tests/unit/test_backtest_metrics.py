from datetime import date
from decimal import Decimal

from app.domain.backtest.metrics import calculate_metrics
from app.domain.execution.costs import CostBreakdown
from app.domain.execution.simulator import Fill


def test_metrics_include_trade_cost_turnover_holding_period_and_period_returns() -> None:
    fills = (
        Fill(
            "FILLED",
            "BUY",
            "AAA",
            100,
            Decimal("10"),
            CostBreakdown(Decimal("1"), Decimal("0"), Decimal("0")),
            execution_date=date(2026, 1, 2),
            industry="tech",
        ),
        Fill(
            "FILLED",
            "SELL",
            "AAA",
            100,
            Decimal("12"),
            CostBreakdown(Decimal("1"), Decimal("0.5"), Decimal("0")),
            execution_date=date(2026, 1, 6),
            industry="tech",
        ),
        Fill(
            "FILLED",
            "BUY",
            "BBB",
            100,
            Decimal("10"),
            CostBreakdown(Decimal("1"), Decimal("0"), Decimal("0")),
            execution_date=date(2026, 1, 3),
            industry="finance",
        ),
        Fill(
            "FILLED",
            "SELL",
            "BBB",
            100,
            Decimal("9"),
            CostBreakdown(Decimal("1"), Decimal("0.5"), Decimal("0")),
            execution_date=date(2026, 1, 7),
            industry="finance",
        ),
    )

    metrics = calculate_metrics(
        [Decimal("1000"), Decimal("1010"), Decimal("1100"), Decimal("1200")],
        fills=fills,
        trade_dates=[date(2026, 1, 2), date(2026, 1, 6), date(2026, 2, 1)],
    )

    assert metrics.win_rate == Decimal("0.5")
    assert metrics.profit_loss_ratio == Decimal("1.926829268292682926829268293")
    assert metrics.average_holding_period == Decimal("4")
    assert metrics.total_cost == Decimal("5.0")
    assert metrics.turnover == Decimal("4.1")
    assert metrics.industry_exposure == (("finance", Decimal("0.5")), ("tech", Decimal("0.5")))
    assert metrics.monthly_returns == (
        ("2026-01", Decimal("0.1")),
        ("2026-02", Decimal("0.090909090909090909090909091")),
    )
    assert metrics.yearly_returns == (("2026", Decimal("0.2")),)
