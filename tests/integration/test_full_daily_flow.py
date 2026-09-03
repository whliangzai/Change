"""External contract tests for the import-to-report daily flow."""

from datetime import date
from pathlib import Path

import pytest

from app.core.errors import StateConflictError
from app.core.security import AccessDeniedError
from tests.support.daily_flow_contract import create_runner, request

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "complete_minimal_dataset"


def test_daily_flow_carries_immutable_versions_and_hash() -> None:
    result = create_runner(FIXTURE_ROOT).run(request())

    assert result.run_id
    assert result.data_version == "dataset-v1"
    assert result.strategy_version == "trend-continuation-v1"
    assert result.cost_version == "cost-v1"
    assert result.rule_version == "cn-equity-t1-v1"
    assert len(result.content_hash) == 64
    assert result.as_of_date == date(2024, 1, 5)
    assert result.completed_stages == (
        "import",
        "quality",
        "historical_universe",
        "signal",
        "risk",
        "t_plus_one_plan",
        "execution",
        "ledger",
        "daily_report",
    )


def test_daily_flow_uses_historical_pool_and_excludes_future_information() -> None:
    result = create_runner(FIXTURE_ROOT).run(request())

    assert result.future_rows == 0
    assert result.survivor_bias_detected is False
    assert "600002.SZ" in result.historical_pool_symbols
    assert "600002.SZ" not in result.signal_symbols
    assert result.as_of_date == date(2024, 1, 5)


def test_daily_flow_builds_t_plus_one_plan_and_records_execution_boundaries() -> None:
    result = create_runner(FIXTURE_ROOT).run(request())

    assert result.plan_execution_dates
    assert all(execution_date > result.as_of_date for execution_date in result.plan_execution_dates)
    assert all(fill.execution_date > result.as_of_date for fill in result.fills)
    assert all(fill.price_source == "NEXT_OPEN_ADJUSTED" for fill in result.fills)


def test_daily_flow_preserves_manual_confirmation_and_actual_fill_as_separate_events() -> None:
    runner = create_runner(FIXTURE_ROOT)
    result = runner.run(request())

    runner.confirm_plan(result.run_id, actor_roles=frozenset({"REVIEWER"}))
    runner.record_actual_fill(
        result.run_id,
        symbol="600001.SZ",
        quantity=100,
        price="10.60",
        actor_roles=frozenset({"USER"}),
    )

    assert result.plan_execution_dates
    assert result.ledger.confirmation_event_count == 1
    assert result.ledger.actual_fill_event_count == 1
    assert result.ledger.plan_was_overwritten is False


def test_daily_flow_enforces_money_lot_cash_and_position_invariants() -> None:
    result = create_runner(FIXTURE_ROOT).run(request())

    assert all(fill.quantity > 0 and fill.quantity % 100 == 0 for fill in result.fills)
    assert result.ledger.cash >= 0
    assert all(quantity >= 0 for quantity in result.ledger.positions.values())
    assert result.ledger.total_fees == "5.04"
    assert result.ledger.is_reconciled is True


def test_daily_flow_reports_unfilled_rules_and_partial_fill_without_fake_completion() -> None:
    result = create_runner(FIXTURE_ROOT).run(request())

    reasons = {fill.unfilled_reason for fill in result.fills if fill.unfilled_reason}
    assert {"SUSPENDED", "LIMIT_UP", "MISSING_PRICE"} <= reasons
    partial = next(fill for fill in result.fills if fill.symbol == "600005.SZ")
    assert partial.status == "PARTIALLY_FILLED"
    assert partial.requested_quantity > partial.filled_quantity > 0
    assert result.daily_report.completed is True


def test_daily_flow_requires_reviewer_for_confirmation_and_keeps_user_fill_allowed() -> None:
    runner = create_runner(FIXTURE_ROOT)
    result = runner.run(request())

    with pytest.raises(AccessDeniedError):
        runner.confirm_plan(result.run_id, actor_roles=frozenset({"USER"}))

    runner.confirm_plan(result.run_id, actor_roles=frozenset({"REVIEWER"}))
    runner.record_actual_fill(
        result.run_id,
        symbol="600001.SZ",
        quantity=100,
        price="10.60",
        actor_roles=frozenset({"USER"}),
    )


def test_daily_flow_replays_same_idempotency_key_without_creating_a_second_run() -> None:
    runner = create_runner(FIXTURE_ROOT)
    first = runner.run(request())
    replay = runner.run(request())

    assert replay.run_id == first.run_id
    assert replay.content_hash == first.content_hash


def test_daily_flow_export_token_is_short_lived_and_single_use() -> None:
    runner = create_runner(FIXTURE_ROOT)
    result = runner.run(request())

    exported = runner.export_once(result.run_id, export_token="export-token-20240105")

    assert exported.export_id
    assert exported.body
    with pytest.raises(StateConflictError):
        runner.export_once(result.run_id, export_token="export-token-20240105")
