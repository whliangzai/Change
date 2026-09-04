"""Verify a backup manifest before a human-approved restore."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select

from app.infrastructure.db import models
from scripts.backup import verify_backup_manifest


def restore_readiness(manifest_path: str | Path, database_url: str | None = None) -> dict[str, Any]:
    """Return an explicit, fail-closed restore readiness report."""
    file_hashes = "VERIFIED" if verify_backup_manifest(manifest_path) else "FAILED"
    if not database_url:
        return {
            "file_hashes": file_hashes,
            "database_migrations": "NOT_CONFIGURED",
            "ledger_reconciliation": "NOT_CHECKED",
            "run_reproducibility": "NOT_CHECKED",
            "ready": False,
        }
    migration_status = _database_migrations(database_url)
    ledger_status = (
        _ledger_reconciliation(database_url) if migration_status == "VERIFIED" else "NOT_CHECKED"
    )
    run_reproducibility = "NOT_CHECKED"
    return {
        "file_hashes": file_hashes,
        "database_migrations": migration_status,
        "ledger_reconciliation": ledger_status,
        "run_reproducibility": run_reproducibility,
        "ready": file_hashes == "VERIFIED"
        and migration_status == "VERIFIED"
        and ledger_status == "VERIFIED"
        and run_reproducibility == "VERIFIED",
    }


def _database_migrations(database_url: str) -> str:
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database_url)
        expected = set(ScriptDirectory.from_config(config).get_heads())
        with create_engine(database_url).connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        return "VERIFIED" if current == expected else "FAILED"
    except Exception:
        return "FAILED"


def _ledger_reconciliation(database_url: str) -> str:
    try:
        engine = create_engine(database_url)
        with engine.connect() as connection:
            snapshots = connection.execute(
                select(
                    models.PortfolioSnapshot.cash,
                    models.PortfolioSnapshot.equity,
                    models.PortfolioSnapshot.market_value,
                )
            ).all()
            if not snapshots:
                return "NOT_CHECKED"
            for snapshot in snapshots:
                if (
                    snapshot.cash < 0
                    or snapshot.equity < 0
                    or snapshot.equity != snapshot.cash + snapshot.market_value
                ):
                    return "FAILED"
        return "VERIFIED"
    except Exception:
        return "FAILED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a backup without restoring it")
    parser.add_argument("manifest", nargs="?", default=os.getenv("BACKUP_MANIFEST"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.manifest:
        parser.error("manifest path is required")
    readiness = restore_readiness(Path(args.manifest), args.database_url)
    print(json.dumps(readiness, ensure_ascii=False, sort_keys=True))
    if readiness["file_hashes"] != "VERIFIED":
        return 1
    return 0 if readiness["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
