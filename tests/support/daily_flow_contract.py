"""Protocol adapter for the data/engine/application integration boundary.

The tests deliberately import the production runner. There is no fallback
implementation here: a missing runner is reported as an integration blocker.
"""

from dataclasses import dataclass
from datetime import date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

import pytest


@dataclass(frozen=True, slots=True)
class DailyFlowRequest:
    as_of_date: date
    information_cutoff_at: datetime
    request_id: str
    idempotency_key: str


class DailyFlowResult(Protocol):
    run_id: str
    data_version: str
    strategy_version: str
    cost_version: str
    rule_version: str
    as_of_date: date
    future_rows: int
    survivor_bias_detected: bool
    content_hash: str
    historical_pool_symbols: tuple[str, ...]
    signal_symbols: tuple[str, ...]
    risk_rejected_symbols: tuple[str, ...]
    plan_execution_dates: tuple[date, ...]
    fills: tuple[Any, ...]
    ledger: Any
    daily_report: Any
    completed_stages: tuple[str, ...]


class ExportResult(Protocol):
    export_id: str
    body: bytes


class DailyFlowRunner(Protocol):
    def run(self, request: DailyFlowRequest) -> DailyFlowResult: ...

    def confirm_plan(self, run_id: str, *, actor_roles: frozenset[str]) -> None: ...

    def record_actual_fill(
        self,
        run_id: str,
        *,
        symbol: str,
        quantity: int,
        price: str,
        actor_roles: frozenset[str],
    ) -> None: ...

    def export_once(self, run_id: str, *, export_token: str) -> ExportResult: ...


def create_runner(fixture_root: Path) -> DailyFlowRunner:
    try:
        factory = import_module("app.application.daily_flow").create_daily_flow_runner
    except (ImportError, AttributeError) as exc:
        pytest.fail(
            "BLOCKED: app.application.daily_flow.create_daily_flow_runner is unavailable; "
            "integrate the data/engine/application implementations before enabling this suite",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return factory(fixture_root)


def request() -> DailyFlowRequest:
    return DailyFlowRequest(
        as_of_date=date(2024, 1, 5),
        information_cutoff_at=datetime.fromisoformat("2024-01-05T12:00:00+00:00"),
        request_id="req-daily-flow",
        idempotency_key="daily-flow-20240105",
    )
