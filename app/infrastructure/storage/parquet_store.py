"""Versioned raw/standardized/report storage with tamper-evident manifests."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    dataset: str
    layer: str
    version: str
    path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int
    row_count: int
    created_at: str
    format: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload["manifest_path"] = str(self.manifest_path)
        return payload


def _canonical_row(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParquetStore:
    """Store immutable artifacts under ``<root>/<layer>/<dataset>/<version>``."""

    _layers = frozenset({"raw", "standardized", "reports"})

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write_records(
        self,
        dataset: str,
        records: Sequence[Mapping[str, Any]],
        *,
        layer: str = "raw",
        version: str | None = None,
    ) -> ArtifactManifest:
        if layer not in self._layers:
            raise ValueError(f"layer must be one of {sorted(self._layers)}")
        dataset_path = Path(dataset)
        if (
            not dataset
            or dataset_path.is_absolute()
            or len(dataset_path.parts) not in {1, 2}
            or any(part in {"", ".", ".."} for part in dataset_path.parts)
        ):
            raise ValueError("dataset must be a safe one- or two-part relative name")
        content, file_format = self._encode(records)
        digest = hashlib.sha256(content).hexdigest()
        artifact_version = version or f"v-{digest[:16]}"
        directory = self.root / layer / dataset / artifact_version
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / f"{dataset_path.name}.parquet"
        path.write_bytes(content)
        manifest_path = directory / "manifest.json"
        manifest = ArtifactManifest(
            dataset=dataset,
            layer=layer,
            version=artifact_version,
            path=path,
            manifest_path=manifest_path,
            sha256=digest,
            size_bytes=len(content),
            row_count=len(records),
            created_at=datetime.now(UTC).isoformat(),
            format=file_format,
        )
        manifest_path.write_text(
            json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest

    def _encode(self, records: Sequence[Mapping[str, Any]]) -> tuple[bytes, str]:
        """Use pyarrow when present; retain a deterministic local fallback for tests."""
        pyarrow = importlib.util.find_spec("pyarrow")
        if pyarrow is not None:
            table_module = importlib.import_module("pyarrow")
            parquet_module = importlib.import_module("pyarrow.parquet")
            table = table_module.Table.from_pylist([dict(row) for row in records])
            import io

            buffer = io.BytesIO()
            parquet_module.write_table(table, buffer)
            return buffer.getvalue(), "parquet"
        lines = "".join(_canonical_row(row) + "\n" for row in records)
        return lines.encode("utf-8"), "jsonl-fallback"

    def read_records(self, manifest_path: str | Path) -> list[dict[str, Any]]:
        manifest = _load_manifest(Path(manifest_path))
        path = _manifest_artifact_path(manifest, Path(manifest_path))
        if manifest.get("format") == "parquet":
            parquet_module = importlib.import_module("pyarrow.parquet")
            return [dict(row) for row in parquet_module.read_table(path).to_pylist()]
        return [
            cast(dict[str, Any], json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
        ]


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def _manifest_artifact_path(manifest: Mapping[str, Any], manifest_path: Path) -> Path:
    candidate = Path(str(manifest.get("path", "")))
    return candidate if candidate.is_absolute() else manifest_path.parent / candidate.name


def verify_manifest(manifest_path: str | Path) -> bool:
    """Verify artifact existence, byte count, and SHA-256 recorded in the manifest."""
    manifest_file = Path(manifest_path)
    manifest = _load_manifest(manifest_file)
    artifact = _manifest_artifact_path(manifest, manifest_file)
    if not manifest or not artifact.is_file():
        return False
    try:
        expected_hash = str(manifest["sha256"])
        expected_size = int(manifest["size_bytes"])
    except (KeyError, TypeError, ValueError):
        return False
    return artifact.stat().st_size == expected_size and _sha256(artifact) == expected_hash


__all__ = ["ArtifactManifest", "ParquetStore", "verify_manifest"]
