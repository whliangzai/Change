from pathlib import Path

from app.infrastructure.storage.parquet_store import (
    ParquetStore,
    verify_manifest,
)


def test_versioned_parquet_artifact_has_hash_manifest_and_detects_tampering(
    tmp_path: Path,
) -> None:
    store = ParquetStore(tmp_path / "data")
    artifact = store.write_records(
        "bars", [{"symbol": "000001.SZ", "trade_date": "2026-09-03", "close": "10.20"}]
    )

    assert artifact.layer == "raw"
    assert artifact.path.suffix == ".parquet"
    assert artifact.sha256
    assert artifact.manifest_path.exists()
    assert verify_manifest(artifact.manifest_path) is True

    artifact.path.write_bytes(artifact.path.read_bytes() + b"tampered")
    assert verify_manifest(artifact.manifest_path) is False
