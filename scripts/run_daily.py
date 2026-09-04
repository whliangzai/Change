"""Run the daily research flow against explicitly configured local data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.data_import_service import DataImportApplicationService
from app.core.contracts import AuditEvent
from app.core.errors import DependencyError
from app.infrastructure.db.session import make_engine
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.infrastructure.repositories.runtime import SqlAlchemyAuditWriter, SqlAlchemyJobRunStore
from app.jobs.tasks import run_daily_report


def main(service: Callable[..., object] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the durable daily research flow")
    parser.add_argument(
        "--business-date", default=os.getenv("BUSINESS_DATE", date.today().isoformat())
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--owner-id", default=os.getenv("DAILY_OWNER_ID"))
    parser.add_argument("--source-path", default=os.getenv("DAILY_SOURCE_PATH"))
    parser.add_argument("--data-batch-id", default=os.getenv("DAILY_DATA_BATCH_ID"))
    parser.add_argument("--strategy-version-id", default=os.getenv("DAILY_STRATEGY_VERSION_ID"))
    parser.add_argument("--source-name", default=os.getenv("DAILY_SOURCE_NAME", "authorized-daily"))
    parser.add_argument("--information-cutoff-at", default=os.getenv("DAILY_INFORMATION_CUTOFF_AT"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(f"dry-run: daily report for {args.business_date}")
        return 0
    run_store = None
    audit_writer = None
    if service is None:
        if (
            not args.database_url
            or not args.owner_id
            or not (args.source_path or args.data_batch_id)
        ):
            print(
                "DEPENDENCY_UNAVAILABLE: daily service is not configured; DATABASE_URL, "
                "DAILY_OWNER_ID and either DAILY_SOURCE_PATH or DAILY_DATA_BATCH_ID are "
                "required for a durable daily run",
                file=sys.stderr,
            )
            return 2
        try:
            owner_id = UUID(args.owner_id)
            strategy_version_id = UUID(args.strategy_version_id or "")
        except ValueError:
            print(
                "invalid DAILY_OWNER_ID or DAILY_STRATEGY_VERSION_ID; expected UUIDs",
                file=sys.stderr,
            )
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
                        request_id="cli:run_daily",
                        result="SUCCESS",
                        after_summary=seed_result,
                    )
                )
            importer = DataImportApplicationService(repository)
            run_store = SqlAlchemyJobRunStore.from_engine(engine)
        except Exception as exc:
            print(f"daily report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        def service(business_date: date) -> object:
            if args.data_batch_id:
                batch = repository.get_batch_quality(args.data_batch_id, owner_id)
                if batch is None:
                    raise DependencyError("configured daily data batch was not found")
            else:
                batch = importer.import_file(
                    owner_id,
                    {
                        "source_name": args.source_name,
                        "data_type": "DAILY_BAR",
                        "file_location": args.source_path,
                        "license_note": "authorized local daily file",
                    },
                    reuse_existing=True,
                )
            if batch["quality_status"] not in {"AVAILABLE", "WARNING_AVAILABLE"}:
                raise DependencyError("daily data batch is unavailable")
            payload = {
                "data_batch_id": batch["batch_id"],
                "strategy_version_id": str(strategy_version_id),
                "as_of_date": business_date.isoformat(),
                "idempotency_key": f"daily:{business_date.isoformat()}",
            }
            if args.information_cutoff_at:
                payload["information_cutoff_at"] = args.information_cutoff_at
            return repository.run_daily_flow(owner_id, payload)

    try:
        result = run_daily_report(
            date.fromisoformat(args.business_date),
            service,
            run_store=run_store,
            audit_writer=audit_writer,
        )
    except Exception as exc:
        print(f"daily report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), default=str, sort_keys=True))
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
