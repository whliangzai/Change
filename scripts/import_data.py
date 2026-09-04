"""Import a user-supplied authorized CSV/JSONL file; never fetch a vendor feed."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_authorized_records(source: str | Path) -> list[dict[str, Any]]:
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() in {".json", ".jsonl"}:
        if path.suffix.lower() == ".jsonl":
            return [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
            ]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON import must contain an array")
        return [dict(item) for item in payload]
    if path.suffix.lower() == ".parquet":
        from app.domain.data.importer import AuthorizedFileImporter

        return [dict(row) for row in AuthorizedFileImporter().import_file(path).rows]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an authorized market-data file")
    parser.add_argument("source", help="path supplied by the user")
    parser.add_argument("--dataset", default="market-data")
    parser.add_argument("--source-name", default=os.getenv("DATA_SOURCE_NAME"))
    parser.add_argument("--data-type", default=os.getenv("DATA_TYPE", "DAILY_BAR"))
    parser.add_argument("--layer", choices=("raw", "standardized", "reports"), default="raw")
    parser.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--owner-id", default=os.getenv("IMPORT_OWNER_ID"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = read_authorized_records(args.source)
    if args.dry_run:
        print(f"dry-run: {len(records)} records; no files written")
        return 0
    if not args.database_url:
        print(
            "DEPENDENCY_UNAVAILABLE: DATABASE_URL is required for a durable import",
            file=sys.stderr,
        )
        return 2
    if not args.owner_id:
        print(
            "DEPENDENCY_UNAVAILABLE: IMPORT_OWNER_ID is required when DATABASE_URL is configured",
            file=sys.stderr,
        )
        return 2
    try:
        owner_id = UUID(args.owner_id)
    except ValueError:
        print("invalid IMPORT_OWNER_ID; expected a UUID", file=sys.stderr)
        return 2
    from app.domain.data.importer import AuthorizedFileImporter
    from app.infrastructure.repositories.research import SqlAlchemyResearchRepository

    imported = (
        AuthorizedFileImporter().import_file(args.source)
        if Path(args.source).suffix.lower() in {".csv", ".parquet"}
        else None
    )
    batch = SqlAlchemyResearchRepository.from_url(
        args.database_url, create_schema=True
    ).import_daily_bars(
        owner_id,
        {
            "source_name": args.source_name or args.dataset,
            "data_type": args.data_type,
            "file_location": str(Path(args.source).resolve()),
            "license_note": "authorized local file",
            "content_hash": imported.content_hash if imported else None,
            "file_hash": imported.file_hash if imported else None,
            "version": imported.version if imported and imported.version else None,
        },
        records,
    )
    print(
        f"imported {len(records)} records; sha256={(imported.file_hash if imported else 'n/a')}; "
        f"batch_status={batch['quality_status']}; batch_id={batch['batch_id']}"
    )
    return 0 if batch["quality_status"] in {"AVAILABLE", "WARNING_AVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
