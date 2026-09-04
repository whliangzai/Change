"""Application orchestration for authorized local market-data imports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import StateConflictError
from app.domain.data.importer import AuthorizedFileImporter


class DailyBarImportRepository(Protocol):
    def import_daily_bars(
        self,
        owner_id: UUID,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> dict[str, Any]: ...

    def find_daily_batch(
        self, owner_id: UUID, payload: dict[str, Any]
    ) -> dict[str, Any] | None: ...


class DataImportApplicationService:
    """Read an explicitly supplied file and delegate durable publication to the repository."""

    def __init__(
        self,
        repository: DailyBarImportRepository,
        importer: AuthorizedFileImporter | None = None,
    ) -> None:
        self._repository = repository
        self._importer = importer or AuthorizedFileImporter()

    def import_file(
        self,
        owner_id: UUID,
        payload: dict[str, Any],
        *,
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        if str(payload.get("data_type", "")).upper() != "DAILY_BAR":
            raise ValueError("only DAILY_BAR imports are supported")
        source = Path(str(payload.get("file_location", "")))
        imported = self._importer.import_file(source)
        import_payload = dict(payload)
        import_payload.update(
            {
                "content_hash": imported.content_hash,
                "file_hash": imported.file_hash,
                "version": payload.get("version") or f"data-{imported.content_hash[:16]}",
            }
        )
        try:
            return self._repository.import_daily_bars(owner_id, import_payload, list(imported.rows))
        except StateConflictError:
            if not reuse_existing:
                raise
            existing = self._repository.find_daily_batch(owner_id, import_payload)
            if existing is None:
                raise
            return {**existing, "replayed": True}
