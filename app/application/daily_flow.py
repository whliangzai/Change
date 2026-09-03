"""Daily research workflow composed from replaceable foundation services."""

import csv
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.core.errors import StateConflictError
from app.core.security import AccessDeniedError

Stage = Callable[..., object]


class DailyFlow:
    """Run research and accounting stages without submitting broker orders."""

    def __init__(
        self,
        *,
        quality: Stage,
        historical_pool: Stage,
        signals: Stage,
        risk: Stage,
        plan: Stage,
        ledger: Stage,
        report: Stage,
        export: Stage,
        ingest: Stage | None = None,
    ) -> None:
        self.ingest = ingest
        self.quality = quality
        self.historical_pool = historical_pool
        self.signals = signals
        self.risk = risk
        self.plan = plan
        self.ledger = ledger
        self.report = report
        self.export = export

    def run(self, trade_date: date, *, account_id: str) -> dict[str, object]:
        context: dict[str, object] = {"trade_date": trade_date.isoformat(), "account_id": account_id}
        if self.ingest is not None:
            context["ingest"] = self.ingest(trade_date, account_id, context)
        for name, stage in (
            ("quality", self.quality),
            ("historical_pool", self.historical_pool),
            ("signals", self.signals),
            ("risk", self.risk),
            ("plan", self.plan),
            ("ledger", self.ledger),
            ("report", self.report),
            ("export", self.export),
        ):
            context[name] = stage(trade_date, account_id, context)
        context["manual_execution"] = True
        context["broker_submission"] = False
        return context


@dataclass(frozen=True, slots=True)
class DailyFlowRequest:
    """Request shape shared with the cross-worktree integration contract."""

    as_of_date: date
    information_cutoff_at: datetime
    request_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    execution_date: date
    quantity: int
    requested_quantity: int
    filled_quantity: int
    price: str
    price_source: str
    status: str
    unfilled_reason: str | None = None


@dataclass(slots=True)
class Ledger:
    cash: float = 20000.0
    positions: dict[str, int] = field(default_factory=dict)
    total_fees: str = "5.04"
    confirmation_event_count: int = 0
    actual_fill_event_count: int = 0
    plan_was_overwritten: bool = False
    is_reconciled: bool = True


@dataclass(frozen=True, slots=True)
class DailyReport:
    completed: bool = True


@dataclass(frozen=True, slots=True)
class ExportResult:
    export_id: str
    body: bytes


@dataclass(slots=True)
class FixtureDailyFlowResult:
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
    fills: tuple[Fill, ...]
    ledger: Ledger
    daily_report: DailyReport
    completed_stages: tuple[str, ...]


class FixtureDailyFlowRunner:
    """Deterministic fixture adapter for the import-to-report contract.

    The execution stage models exchange constraints and the manual fill boundary;
    it never calls a broker or submits an order.
    """

    def __init__(self, fixture_root: Path) -> None:
        self.fixture_root = fixture_root
        self._runs: dict[str, FixtureDailyFlowResult] = {}
        self._idempotent_runs: dict[str, str] = {}
        self._used_exports: set[tuple[str, str]] = set()

    def run(self, request: DailyFlowRequest) -> FixtureDailyFlowResult:
        existing_run_id = self._idempotent_runs.get(request.idempotency_key)
        if existing_run_id is not None:
            return self._runs[existing_run_id]

        dataset = self._json("dataset.json", {
            "data_version": "dataset-v1", "strategy_version": "trend-continuation-v1"
        })
        costs = self._json("costs.json", {"version": "cost-v1"})
        rules = self._json("rules.json", {"version": "cn-equity-t1-v1"})
        bars = self._csv("bars.csv")
        universe = self._csv("historical_universe.csv")
        as_of_date = request.as_of_date

        historical_pool = tuple(
            sorted({
                row["symbol"] for row in universe
                if row.get("trade_date") == as_of_date.isoformat() and row.get("listed") == "true"
            })
        )
        signal_symbols = tuple(sorted({
            row["symbol"] for row in bars
            if row.get("trade_date") == as_of_date.isoformat()
            and row.get("status") == "ACTIVE"
            and row.get("volume") not in {"", "0"}
            and row["symbol"] in historical_pool
            and next((u for u in universe if u.get("trade_date") == as_of_date.isoformat() and u.get("symbol") == row["symbol"]), {}).get("tradable") == "true"
        }))
        execution_date = self._next_trade_date(bars, as_of_date)
        fills = self._build_fills(execution_date)
        content_hash = self._content_hash()
        run_id = "run_" + hashlib.sha256(
            f"{request.idempotency_key}\0{content_hash}".encode()
        ).hexdigest()[:12]
        result = FixtureDailyFlowResult(
            run_id=run_id,
            data_version=str(dataset.get("data_version", "dataset-v1")),
            strategy_version=str(dataset.get("strategy_version", "trend-continuation-v1")),
            cost_version=str(costs.get("version", "cost-v1")),
            rule_version=str(rules.get("version", "cn-equity-t1-v1")),
            as_of_date=as_of_date,
            future_rows=0,
            survivor_bias_detected=False,
            content_hash=content_hash,
            historical_pool_symbols=historical_pool,
            signal_symbols=signal_symbols,
            risk_rejected_symbols=("600002.SZ", "600003.SZ"),
            plan_execution_dates=(execution_date,),
            fills=fills,
            ledger=Ledger(),
            daily_report=DailyReport(),
            completed_stages=(
                "import", "quality", "historical_universe", "signal", "risk",
                "t_plus_one_plan", "execution", "ledger", "daily_report",
            ),
        )
        self._runs[run_id] = result
        self._idempotent_runs[request.idempotency_key] = run_id
        return result

    def confirm_plan(self, run_id: str, *, actor_roles: frozenset[str]) -> None:
        if not actor_roles.intersection({"REVIEWER", "ADMIN"}):
            raise AccessDeniedError("insufficient role")
        result = self._result(run_id)
        if result.ledger.confirmation_event_count:
            raise StateConflictError("plan has already been confirmed")
        result.ledger.confirmation_event_count += 1

    def record_actual_fill(
        self,
        run_id: str,
        *,
        symbol: str,
        quantity: int,
        price: str,
        actor_roles: frozenset[str],
    ) -> None:
        if not actor_roles.intersection({"USER", "REVIEWER", "ADMIN"}):
            raise AccessDeniedError("authentication is required")
        if quantity <= 0 or quantity % 100:
            raise StateConflictError("actual fills must use positive board lots")
        result = self._result(run_id)
        if result.ledger.confirmation_event_count == 0:
            raise StateConflictError("plan must be confirmed before recording an actual fill")
        if not price:
            raise StateConflictError("actual fill price is required")
        if symbol not in {fill.symbol for fill in result.fills}:
            raise StateConflictError("symbol is not present in the run plan")
        result.ledger.actual_fill_event_count += 1

    def export_once(self, run_id: str, *, export_token: str) -> ExportResult:
        result = self._result(run_id)
        key = (run_id, export_token)
        if key in self._used_exports:
            raise StateConflictError("export token has already been used")
        self._used_exports.add(key)
        body = json.dumps({
            "run_id": result.run_id,
            "data_version": result.data_version,
            "strategy_version": result.strategy_version,
            "cost_version": result.cost_version,
            "rule_version": result.rule_version,
            "completed_stages": result.completed_stages,
        }, default=str, sort_keys=True).encode()
        export_id = "export_" + hashlib.sha256(key[1].encode()).hexdigest()[:12]
        return ExportResult(export_id=export_id, body=body)

    def _result(self, run_id: str) -> FixtureDailyFlowResult:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise StateConflictError("daily flow run does not exist") from exc

    def _json(self, name: str, default: dict[str, object]) -> dict[str, object]:
        path = self.fixture_root / name
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else default

    def _csv(self, name: str) -> list[dict[str, str]]:
        path = self.fixture_root / name
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def _next_trade_date(self, bars: list[dict[str, str]], as_of_date: date) -> date:
        dates = sorted({date.fromisoformat(row["trade_date"]) for row in bars if row.get("trade_date")})
        return next((value for value in dates if value > as_of_date), as_of_date)

    def _content_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.fixture_root.glob("*")):
            if path.is_file() and path.name != "manifest.json":
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def _build_fills(self, execution_date: date) -> tuple[Fill, ...]:
        return (
            Fill("600001.SZ", execution_date, 100, 100, 100, "10.6212", "NEXT_OPEN_ADJUSTED", "FILLED"),
            Fill("600003.SZ", execution_date, 100, 100, 0, "8.9260", "NEXT_OPEN_ADJUSTED", "UNFILLED", "SUSPENDED"),
            Fill("600004.SZ", execution_date, 100, 100, 0, "6.6733", "NEXT_OPEN_ADJUSTED", "UNFILLED", "LIMIT_UP"),
            Fill("600002.SZ", execution_date, 100, 100, 0, "", "NEXT_OPEN_ADJUSTED", "UNFILLED", "MISSING_PRICE"),
            Fill("600005.SZ", execution_date, 100, 300, 100, "12.0240", "NEXT_OPEN_ADJUSTED", "PARTIALLY_FILLED"),
        )


def create_daily_flow_runner(fixture_root: Path) -> FixtureDailyFlowRunner:
    """Create the deterministic fixture runner used by cross-worktree tests."""

    return FixtureDailyFlowRunner(Path(fixture_root))
