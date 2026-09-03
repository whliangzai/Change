"""Create and verify content-addressed application backups."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BackupManifest:
    backup_id: str
    source: str
    created_at: str
    files: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(
    source: str | Path, destination: str | Path, backup_id: str | None = None
) -> BackupManifest:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(source_path)
    if source_path == destination_path or source_path in destination_path.parents:
        raise ValueError("backup destination must not be inside the source")
    stamp = backup_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = destination_path / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    for source_file in sorted(path for path in source_path.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source_path)
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
        entries.append(
            {
                "path": relative.as_posix(),
                "size_bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    manifest = BackupManifest(
        backup_id=stamp,
        source=str(source_path),
        created_at=datetime.now(UTC).isoformat(),
        files=tuple(entries),
    )
    (backup_dir / "backup-manifest.json").write_text(
        json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_backup_manifest(manifest_path: str | Path) -> bool:
    path = Path(manifest_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        backup_dir = path.parent
        files = payload["files"]
        for entry in files:
            artifact = backup_dir / str(entry["path"])
            if not artifact.is_file() or artifact.stat().st_size != int(entry["size_bytes"]):
                return False
            if _sha256(artifact) != str(entry["sha256"]):
                return False
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


def main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Create a verified local backup")
    parser.add_argument("--source", default=os.getenv("DATA_ROOT", "data"))
    parser.add_argument("--destination", default=os.getenv("BACKUP_ROOT", "backups"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    source = Path(args.source)
    if args.dry_run:
        count = sum(1 for path in source.rglob("*") if path.is_file()) if source.is_dir() else 0
        print(f"dry-run: {count} files eligible")
        return 0
    manifest = create_backup(source, args.destination)
    print(f"backup {manifest.backup_id}: {len(manifest.files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
