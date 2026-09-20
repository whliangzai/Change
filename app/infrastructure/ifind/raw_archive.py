"""Immutable, credential-free raw response archival for iFinD."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.infrastructure.storage import ArtifactManifest, ParquetStore


def _redact(value: Any, secrets: Sequence[str], key: str = "") -> Any:
    if any(marker in key.lower() for marker in ("token", "authorization", "password", "secret")):
        return "***"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, secrets, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "***")
        return result
    return value


class IFindRawArchive:
    """Store each supplier response as an immutable content-addressed raw artifact."""

    def __init__(self, root: str | Path, *, secret_values: Sequence[str] = ()) -> None:
        self._store = ParquetStore(root)
        self._secrets = tuple(secret_values)

    def archive(
        self,
        interface_name: str,
        request_body: Mapping[str, Any],
        http_status: int,
        response_json: Any,
        *,
        mapping_version: str,
        pulled_at: datetime | None = None,
    ) -> ArtifactManifest:
        redacted_request = _redact(dict(request_body), self._secrets)
        redacted_response = _redact(response_json, self._secrets)
        canonical_response = json.dumps(
            redacted_response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        response_hash = hashlib.sha256(canonical_response.encode("utf-8")).hexdigest()
        row = {
            "interface_name": interface_name,
            "request_json": json.dumps(
                redacted_request, ensure_ascii=False, sort_keys=True, default=str
            ),
            "http_status": http_status,
            "response_json": canonical_response,
            "pulled_at": (pulled_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "response_hash": response_hash,
            "mapping_version": mapping_version,
        }
        version = f"v-{response_hash[:16]}"
        try:
            return self._store.write_records(interface_name, [row], layer="raw", version=version)
        except FileExistsError:
            # Content-addressing makes replay idempotent while preserving immutability.
            manifest_path = self._store.root / "raw" / interface_name / version / "manifest.json"
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


__all__ = ["IFindRawArchive"]
