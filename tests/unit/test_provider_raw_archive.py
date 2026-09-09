import json

from app.infrastructure.providers import RawResponseArchive
from app.infrastructure.storage import ParquetStore


def test_provider_archive_is_content_addressed_by_provider_dataset_and_redacts_token(
    tmp_path,
) -> None:
    archive = RawResponseArchive(tmp_path, secret_values=("private-token",))
    manifest = archive.archive(
        "tushare",
        "daily_bars",
        {"token": "private-token"},
        200,
        {"token": "private-token", "data": [{"close": 10}]},
        mapping_version="v1",
    )
    raw = ParquetStore(tmp_path).read_records(manifest.manifest_path)[0]
    assert manifest.manifest_path.parts[-5:-2] == ("raw", "tushare", "daily_bars")
    assert "private-token" not in json.dumps(raw)
    assert raw["provider"] == "tushare"
