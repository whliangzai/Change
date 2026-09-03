"""Verify a backup manifest before a human-approved restore."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup import verify_backup_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a backup without restoring it")
    parser.add_argument("manifest", nargs="?", default=os.getenv("BACKUP_MANIFEST"))
    args = parser.parse_args()
    if not args.manifest:
        parser.error("manifest path is required")
    ok = verify_backup_manifest(Path(args.manifest))
    print("restore-check: valid" if ok else "restore-check: INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
