"""Replaceable in-memory application boundary for the v1 HTTP contracts."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request

from app.core.contracts import AuditEvent, AuditWriter
from app.core.errors import RuleViolationError, StateConflictError


def _page(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(items),
    }


class InMemoryResearchRepository:
    """Small adapter used until data/engine implementations are connected."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.batches: dict[str, dict[str, Any]] = {
            "batch_available": {
                "batch_id": "batch_available",
                "owner_id": None,
                "quality_status": "AVAILABLE",
                "data_date": "2026-09-03",
                "source_name": "foundation",
                "data_type": "DAILY_BAR",
                "version": "data-v1",
                "record_count": 0,
            }
        }
        self.strategies: dict[str, dict[str, Any]] = {
            "strategy_published": {
                "strategy_version_id": "strategy_published",
                "owner_id": None,
                "name": "foundation strategy",
                "status": "PUBLISHED",
                "version": "v1",
                "parameters": {},
            }
        }
        self.runs: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {
            "plan_001": {
                "plan_id": "plan_001",
                "owner_id": None,
                "execution_date": "2026-09-04",
                "symbol": "600000.SH",
                "side": "BUY",
                "quantity": 300,
                "reference_price": "12.30",
                "status": "PENDING_CONFIRMATION",
                "version": 1,
                "risk_state": "NORMAL",
                "notice": "计划不等于成交；模拟价不是委托价。",
            }
        }
        self.executions: dict[str, dict[str, Any]] = {}
        self.exports: dict[str, dict[str, Any]] = {}
        self.daily_bars: list[dict[str, Any]] = [
            {
                "symbol": "600000.SH",
                "exchange": "SSE",
                "trade_date": "2026-09-03",
                "raw_open": "10.000000",
                "raw_high": "10.200000",
                "raw_low": "9.900000",
                "raw_close": "10.100000",
                "adjusted_open": "10.000000",
                "adjusted_high": "10.200000",
                "adjusted_low": "9.900000",
                "adjusted_close": "10.100000",
                "volume": "10000.000000",
                "amount": "101000.000000",
                "adjust_factor": "1.0000000000",
                "available_at": "2026-09-03T18:00:00+00:00",
                "data_batch_id": "batch_available",
            }
        ]

    def create_batch(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = f"db_{uuid4().hex[:12]}"
        record = {
            "batch_id": batch_id,
            "owner_id": str(owner_id),
            "quality_status": "VALIDATING",
            "data_date": payload.get("date_to") or date.today().isoformat(),
            "version": "pending",
            "record_count": 0,
            **payload,
        }
        with self._lock:
            self.batches[batch_id] = record
        return deepcopy(record)

    def get_batch_quality(self, batch_id: str, owner_id: UUID) -> dict[str, Any] | None:
        return self._owned(self.batches.get(batch_id), owner_id)

    def list_batches(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        rows = [
            deepcopy(batch)
            for batch in self.batches.values()
            if batch.get("owner_id") == str(owner_id)
            and (status is None or batch.get("quality_status") == status)
        ]
        return _page(rows, page, page_size)

    def list_pool(
        self,
        trade_date: date,
        status: str | None,
        page: int,
        page_size: int,
        owner_id: UUID | None = None,
    ) -> dict[str, Any]:
        rows = [
            {
                "symbol": "600000.SH",
                "exchange": "SSE",
                "board": "MAIN",
                "in_pool": True,
                "exclusion_reasons": [],
                "listed_trade_days": 3000,
                "amount_median_20": "100000000.00",
                "status_as_of": "NORMAL",
                "source_batch_id": "batch_available",
                "trade_date": trade_date.isoformat(),
            }
        ]
        if status == "EXCLUDED":
            rows = []
        elif status == "ELIGIBLE":
            rows = [row for row in rows if row["in_pool"]]
        return _page(rows, page, page_size)

    def list_daily_bars(
        self,
        trade_date: date,
        symbol: str | None,
        page: int,
        page_size: int,
        owner_id: UUID,
    ) -> dict[str, Any]:
        rows = [
            deepcopy(row)
            for row in self.daily_bars
            if row["trade_date"] == trade_date.isoformat()
            and (symbol is None or row["symbol"] == symbol)
        ]
        return _page(rows, page, page_size)

    def create_strategy(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_id = f"strat_{uuid4().hex[:12]}"
        record = {
            "strategy_version_id": strategy_id,
            "owner_id": str(owner_id),
            "status": "DRAFT",
            "version": "v1",
            **payload,
        }
        with self._lock:
            self.strategies[strategy_id] = record
        return deepcopy(record)

    def submit_strategy(
        self,
        strategy_id: str,
        owner_id: UUID,
        decision: str,
        note: str,
        *,
        allow_reviewer: bool = False,
    ) -> dict[str, Any] | None:
        candidate = self.strategies.get(strategy_id)
        record = candidate if allow_reviewer else self._owned(candidate, owner_id)
        if record is None:
            return None
        if record["status"] in {"PUBLISHED", "ARCHIVED"}:
            raise StateConflictError("published strategy versions cannot be edited")
        record["status"] = {"SUBMIT": "PENDING_REVIEW", "PUBLISH": "PUBLISHED", "REJECT": "DRAFT"}[
            decision
        ]
        record["review_note"] = note
        return deepcopy(record)

    def strategy_diff(self, strategy_id: str, owner_id: UUID) -> dict[str, Any] | None:
        record = self._owned(self.strategies.get(strategy_id), owner_id)
        if record is None:
            return None
        return {
            **deepcopy(record),
            "strategy_version_id": strategy_id,
            "base_version": None,
            "changes": record.get("parameters", {}),
        }

    def list_strategies(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        rows = [
            deepcopy(record)
            for record in self.strategies.values()
            if record.get("owner_id") == str(owner_id)
            and (status is None or record.get("status") == status)
        ]
        rows.sort(key=lambda record: str(record["strategy_version_id"]))
        return _page(rows, page, page_size)

    def dependencies_available(self, payload: dict[str, Any]) -> bool:
        batch = self.batches.get(payload["data_batch_id"])
        strategy = self.strategies.get(payload["strategy_version_id"])
        return bool(
            batch
            and batch.get("quality_status") in {"AVAILABLE", "WARNING_AVAILABLE"}
            and strategy
            and strategy.get("status") == "PUBLISHED"
            and payload["cost_config_id"] == "cost_v1"
            and payload["rule_config_id"] == "rule_v1"
        )

    def create_run(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = f"run_{uuid4().hex[:12]}"
        record = {
            "run_id": run_id,
            "owner_id": str(owner_id),
            "status": "QUEUED",
            "result_usable": False,
            "stages": [],
            **payload,
        }
        with self._lock:
            self.runs[run_id] = record
        return deepcopy(record)

    def get_run(self, run_id: str, owner_id: UUID) -> dict[str, Any] | None:
        return self._owned(self.runs.get(run_id), owner_id)

    def list_backtests(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        rows = [
            deepcopy(run)
            for run in self.runs.values()
            if run.get("owner_id") == str(owner_id)
            and (status is None or run.get("status") == status)
        ]
        return _page(rows, page, page_size)

    def get_run_child(self, run_id: str, owner_id: UUID, kind: str) -> dict[str, Any] | None:
        record = self.get_run(run_id, owner_id)
        if record is None:
            return None
        if kind == "trades":
            return {"items": [], "page": 1, "page_size": 50, "total": 0}
        return {
            "run_id": run_id,
            "result_usable": False,
            "status": record["status"],
            "metrics": {},
            "unavailable_reasons": [],
        }

    def list_plans(
        self, execution_date: date, page: int, page_size: int, owner_id: UUID | None = None
    ) -> dict[str, Any]:
        rows = [
            deepcopy(p)
            for p in self.plans.values()
            if p["execution_date"] == execution_date.isoformat()
            and (owner_id is None or p.get("owner_id") in {None, str(owner_id)})
        ]
        return _page(rows, page, page_size)

    def confirm_plan(
        self, plan_id: str, owner_id: UUID, decision: str, expected_version: int, note: str
    ) -> dict[str, Any] | None:
        plan = self._owned(self.plans.get(plan_id), owner_id)
        if plan is None:
            return None
        if plan["version"] != expected_version:
            raise StateConflictError("plan version does not match")
        if plan["status"] != "PENDING_CONFIRMATION":
            raise StateConflictError("plan is no longer awaiting confirmation")
        if plan["risk_state"] in {"STOP_NEW", "MANUAL_REVIEW"} and decision == "CONFIRM":
            raise RuleViolationError("risk state does not allow new positions")
        plan["status"] = "CONFIRMED" if decision == "CONFIRM" else "SKIPPED"
        plan["version"] += 1
        plan["review_note"] = note
        return deepcopy(plan)

    def create_execution(self, payload: dict[str, Any], owner_id: UUID) -> dict[str, Any] | None:
        plan = self._owned(self.plans.get(payload["plan_id"]), owner_id)
        if plan is None:
            return None
        if plan["status"] not in {"CONFIRMED", "PARTIALLY_FILLED"}:
            raise StateConflictError("plan must be confirmed before recording execution")
        if payload["quantity"] + payload["unfilled_quantity"] != plan["quantity"]:
            raise RuleViolationError("filled and unfilled quantities must equal planned quantity")
        execution_id = f"exec_{uuid4().hex[:12]}"
        status = "FILLED" if payload["unfilled_quantity"] == 0 else "PARTIALLY_FILLED"
        record = {
            "execution_id": execution_id,
            "status": status,
            "plan_id": plan["plan_id"],
            **payload,
        }
        self.executions[execution_id] = record
        plan["status"] = status
        return deepcopy(record)

    def daily_report(self, report_date: date, owner_id: UUID | None = None) -> dict[str, Any]:
        return {
            "report_date": report_date.isoformat(),
            "run_id": None,
            "data_quality": "AVAILABLE",
            "market_switch": "ON",
            "account": {"equity": "20000.00", "cash": "20000.00"},
            "holdings": [],
            "candidates": [],
            "order_plans": list(self.plans.values()),
            "risk_state": "NORMAL",
            "actual_execution_input": True,
            "notice": "研究用途；不构成投资建议；不承诺收益；不自动下单。",
        }

    def snapshots(self, account_id: str, page: int, page_size: int) -> dict[str, Any]:
        return _page(
            [
                {
                    "account_id": account_id,
                    "snapshot_date": date.today().isoformat(),
                    "equity": "20000.00",
                    "cash": "20000.00",
                    "holdings_value": "0.00",
                }
            ],
            page,
            page_size,
        )

    def create_export(self, report_id: str, owner_id: UUID) -> dict[str, Any]:
        export_id = f"exp_{uuid4().hex[:12]}"
        token = uuid4().hex
        record = {
            "export_id": export_id,
            "report_id": report_id,
            "owner_id": str(owner_id),
            "status": "READY",
            "download_token": token,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        }
        self.exports[export_id] = record
        return deepcopy(record)

    def get_export(self, export_id: str, owner_id: UUID | None = None) -> dict[str, Any] | None:
        record = self.exports.get(export_id)
        if owner_id is not None and record is not None and record.get("owner_id") != str(owner_id):
            return None
        return deepcopy(record) if record is not None else None

    def _owned(self, value: dict[str, Any] | None, owner_id: UUID) -> dict[str, Any] | None:
        if value is None or value.get("owner_id") not in {None, str(owner_id)}:
            return None
        return value


def repository(request: Request) -> InMemoryResearchRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def audit(
    request: Any,
    principal: Any,
    action: str,
    object_type: str,
    object_id: str | None,
    result: str,
    *,
    idempotency_key: str | None = None,
    before_summary: dict[str, Any] | None = None,
    after_summary: dict[str, Any] | None = None,
) -> None:
    writer: AuditWriter = request.app.state.audit_writer
    writer.append(
        AuditEvent(
            occurred_at=datetime.now(UTC),
            actor_id=principal.user_id if principal else None,
            actor_roles=tuple(sorted(role.value for role in principal.roles)) if principal else (),
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=request.state.request_id,
            result=result,
            idempotency_key=(
                idempotency_key
                if idempotency_key is not None
                else getattr(request.state, "idempotency_key", None)
            ),
            before_summary=before_summary,
            after_summary=after_summary,
        )
    )
