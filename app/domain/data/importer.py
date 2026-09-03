"""Authorized local CSV/Parquet ingestion with immutable content identity."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

import pandas as pd


@dataclass(frozen=True, slots=True)
class ImportedDataset:
    source_path: Path
    content_hash: str
    rows: tuple[dict[str, object], ...]
    dataset_type: str | None = None
    as_of_date: date | None = None
    available_at: datetime | None = None
    information_cutoff_at: datetime | None = None
    version: str | None = None

    @property
    def content_sha256(self) -> str:
        return self.content_hash


class AuthorizedFileImporter:
    """Read only an explicitly supplied local file; no supplier/network discovery exists."""

    _suffixes: Final[set[str]] = {".csv", ".parquet"}

    def import_file(
        self,
        path: str | Path,
        *,
        dataset_type: str | None = None,
        as_of_date: date | None = None,
        available_at: datetime | None = None,
        information_cutoff_at: datetime | None = None,
        version: str | None = None,
    ) -> ImportedDataset:
        source_path = Path(path).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if source_path.suffix.lower() not in self._suffixes:
            raise ValueError("only explicitly supplied .csv and .parquet files are accepted")
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        if information_cutoff_at is not None and information_cutoff_at.tzinfo is None:
            raise ValueError("information_cutoff_at must be timezone-aware")
        if (
            available_at is not None
            and information_cutoff_at is not None
            and information_cutoff_at > available_at
        ):
            raise ValueError("information_cutoff_at cannot be later than available_at")
        content = source_path.read_bytes()
        frame = (
            pd.read_csv(source_path)
            if source_path.suffix.lower() == ".csv"
            else pd.read_parquet(source_path)
        )
        rows = tuple(self._record(row) for row in frame.to_dict(orient="records"))
        return ImportedDataset(
            source_path=source_path,
            content_hash=hashlib.sha256(content).hexdigest(),
            rows=rows,
            dataset_type=dataset_type,
            as_of_date=as_of_date,
            available_at=available_at,
            information_cutoff_at=information_cutoff_at,
            version=version,
        )

    @staticmethod
    def _record(row: Mapping[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in row.items():
            result[str(key)] = None if pd.isna(value) else value
        return result
