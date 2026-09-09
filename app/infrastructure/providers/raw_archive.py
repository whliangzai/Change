"""Credential-free, immutable raw-response archival for every data provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.infrastructure.storage import ArtifactManifest, ParquetStore


def redact_secrets(value: Any, secrets: Sequence[str], key: str = "") -> Any:
    """Remove credentials from both request summaries and supplier responses."""
    if any(marker in key.lower() for marker in ("token", "authorization", "password", "secret")):
        return "***"
    if isinstance(value, Mapping):
        return {str(k): redact_secrets(v, secrets, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "***")
    return value


class RawResponseArchive:
    """Content-addressed raw archive under ``raw/<provider>/<dataset>/<hash>``."""

    def __init__(self, root: str | Path, *, secret_values: Sequence[str] = ()) -> None:
        self._store = ParquetStore(root)
        self._secrets = tuple(secret_values)

    def archive(
        self,
        provider: str,
        dataset: str,
        request_summary: Mapping[str, Any],
        http_status: int | None,
        response: Any,
        *,
        mapping_version: str,
        pulled_at: datetime | None = None,
    ) -> ArtifactManifest:
        if not provider or Path(provider).name != provider:
            raise ValueError("provider must be a simple name")
        request = redact_secrets(dict(request_summary), self._secrets)
        body = redact_secrets(response, self._secrets)
        response_json = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        response_hash = hashlib.sha256(response_json.encode("utf-8")).hexdigest()
        row = {
            "provider": provider,
            "dataset": dataset,
            "request_summary": json.dumps(request, ensure_ascii=False, sort_keys=True, default=str),
            "http_status": http_status,
            "response_json": response_json,
            "pulled_at": (pulled_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "response_hash": response_hash,
            "mapping_version": mapping_version,
        }
        archive_dataset = f"{provider}/{dataset}"
        version = f"v-{response_hash[:16]}"
        try:
            return self._store.write_records(archive_dataset, [row], layer="raw", version=version)
        except FileExistsError:
            manifest_path = (
                self._store.root / "raw" / provider / dataset / version / "manifest.json"
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            return ArtifactManifest(
                dataset=str(payload["dataset"]),
                layer=str(payload["layer"]),
                version=str(payload["version"]),
                path=Path(str(payload["path"])),
                manifest_path=Path(str(payload["manifest_path"])),
                sha256=str(payload["sha256"]),
                size_bytes=int(payload["size_bytes"]),
                row_count=int(payload["row_count"]),
                created_at=str(payload["created_at"]),
                format=str(payload["format"]),
            )


__all__ = ["RawResponseArchive", "redact_secrets"]
