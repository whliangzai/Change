"""Execute a persisted backtest from an explicitly configured local database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.contracts import AuditEvent
from app.core.errors import DependencyError
from app.infrastructure.db.session import make_engine
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.infrastructure.repositories.runtime import SqlAlchemyAuditWriter, SqlAlchemyJobRunStore
from app.jobs.tasks import run_backtest as run_backtest_job
from app.schemas.backtest import BacktestCreate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or preview a durable backtest")
    parser.add_argument(
        "--business-date", default=os.getenv("BUSINESS_DATE", date.today().isoformat())
    )
    parser.add_argument("--scope", default=os.getenv("BACKTEST_SCOPE", "default"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--owner-id", default=os.getenv("BACKTEST_OWNER_ID"))
    parser.add_argument("--data-batch-id", default=os.getenv("BACKTEST_DATA_BATCH_ID"))
    parser.add_argument("--strategy-version-id", default=os.getenv("BACKTEST_STRATEGY_VERSION_ID"))
    parser.add_argument("--start-date", default=os.getenv("BACKTEST_START_DATE"))
    parser.add_argument("--end-date", default=os.getenv("BACKTEST_END_DATE"))
    parser.add_argument("--train-end", default=os.getenv("BACKTEST_TRAIN_END"))
    parser.add_argument("--valid-end", default=os.getenv("BACKTEST_VALID_END"))
    parser.add_argument("--oos-start", default=os.getenv("BACKTEST_OOS_START"))
    parser.add_argument(
        "--benchmark-symbol", default=os.getenv("BACKTEST_BENCHMARK_SYMBOL", "000300.SH")
    )
    parser.add_argument(
        "--initial-equity", default=os.getenv("BACKTEST_INITIAL_EQUITY", "20000.00")
    )
    parser.add_argument("--cost-config-id", default=os.getenv("BACKTEST_COST_CONFIG_ID", "cost_v1"))
    parser.add_argument("--rule-config-id", default=os.getenv("BACKTEST_RULE_CONFIG_ID", "rule_v1"))
    parser.add_argument(
        "--information-cutoff-at", default=os.getenv("BACKTEST_INFORMATION_CUTOFF_AT")
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _payload(args: argparse.Namespace) -> dict[str, object]:
    values = {
        "data_batch_id": args.data_batch_id,
        "strategy_version_id": args.strategy_version_id,
        "cost_config_id": args.cost_config_id,
        "rule_config_id": args.rule_config_id,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "train_end": args.train_end,
        "valid_end": args.valid_end,
        "oos_start": args.oos_start,
        "benchmark_symbol": args.benchmark_symbol,
        "initial_equity": args.initial_equity,
        "mode": "BACKTEST",
    }
    if any(value in {None, ""} for value in values.values()):
        raise ValueError(
            "BACKTEST_DATA_BATCH_ID, BACKTEST_STRATEGY_VERSION_ID and all backtest dates are required"
        )
    payload = BacktestCreate.model_validate(values).model_dump(mode="json")
    if args.information_cutoff_at:
        payload["information_cutoff_at"] = args.information_cutoff_at
    return payload


def _run_no(owner_id: UUID, scope: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        {"owner_id": str(owner_id), "scope": scope, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"run_{sha256(encoded.encode()).hexdigest()[:32]}"


def main(service: Callable[..., object] | None = None) -> int:
    args = _parser().parse_args()
    try:
        business_date = date.fromisoformat(args.business_date)
    except ValueError as exc:
        print(f"invalid business date: {exc}", file=sys.stderr)
        return 2

    if service is not None:
        if args.dry_run:
            print(f"dry-run: backtest {args.scope} for {args.business_date}")
            return 0
        try:
            result = run_backtest_job(business_date, service, scope=args.scope)
        except Exception as exc:
            print(f"backtest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(asdict(result), default=str, sort_keys=True))
        return 0 if result.status == "succeeded" else 1

    if not args.database_url or not args.owner_id:
        print(
            "DEPENDENCY_UNAVAILABLE: backtest service is not configured; DATABASE_URL and "
            "BACKTEST_OWNER_ID are required for a durable backtest",
            file=sys.stderr,
        )
        return 2
    try:
        owner_id = UUID(args.owner_id)
        payload = _payload(args)
    except (ValueError, TypeError) as exc:
        print(f"invalid backtest configuration: {exc}", file=sys.stderr)
        return 2

    try:
        engine = make_engine(args.database_url)
        repository = SqlAlchemyResearchRepository.from_engine(engine, create_schema=True)
        audit_writer = SqlAlchemyAuditWriter.from_engine(engine)
        seed_result = repository.initialize_local_default_versions()
        if seed_result["created"]:
            audit_writer.append(
                AuditEvent(
                    occurred_at=datetime.now(UTC),
                    actor_id=None,
                    actor_roles=("SYSTEM",),
                    action="LOCAL_DEFAULT_CONFIG_INITIALIZE",
                    object_type="configuration_versions",
                    object_id="cost_v1,rule_v1",
                    request_id="cli:run_backtest",
                    result="SUCCESS",
                    after_summary=seed_result,
                )
            )
        if args.dry_run:
            validation = repository.validate_backtest(owner_id, payload)
            if not validation["available"]:
                raise DependencyError(str(validation["reason"]))
            print(json.dumps({"status": "dry-run", **validation}, default=str, sort_keys=True))
            return 0
        if not repository.dependencies_available(payload | {"owner_id": str(owner_id)}):
            raise DependencyError("backtest prerequisites are unavailable")
        run_store = SqlAlchemyJobRunStore.from_engine(engine)
        run_no = _run_no(owner_id, args.scope, payload)

        def configured_service(*, business_date: date) -> object:
            del business_date
            run = repository.create_run(owner_id, payload | {"run_no": run_no})
            result = repository.execute_run(run["run_id"], owner_id)
            if result is None:
                raise DependencyError("backtest run disappeared before execution")
            if result["status"] != "SUCCEEDED":
                raise DependencyError(
                    str(result.get("result_summary", {}).get("reason", result["status"]))
                )
            return result

        result = run_backtest_job(
            business_date,
            configured_service,
            scope=args.scope,
            run_store=run_store,
            audit_writer=audit_writer,
        )
    except Exception as exc:
        print(f"backtest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), default=str, sort_keys=True))
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
