"""Import a user-supplied authorized CSV/JSONL file; never fetch a vendor feed."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_authorized_records(source: str | Path) -> list[dict[str, Any]]:
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() in {".json", ".jsonl"}:
        if path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON import must contain an array")
        return [dict(item) for item in payload]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an authorized market-data file")
    parser.add_argument("source", help="path supplied by the user")
    parser.add_argument("--dataset", default="market-data")
    parser.add_argument("--layer", choices=("raw", "standardized", "reports"), default="raw")
    parser.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = read_authorized_records(args.source)
    if args.dry_run:
        print(f"dry-run: {len(records)} records; no files written")
        return 0
    from app.infrastructure.storage.parquet_store import ParquetStore

    artifact = ParquetStore(args.data_root).write_records(args.dataset, records, layer=args.layer)
    print(f"imported {artifact.row_count} records; sha256={artifact.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
