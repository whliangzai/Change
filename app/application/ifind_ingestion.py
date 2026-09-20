"""Application orchestration for auditable iFinD HTTP daily imports."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import DependencyError
from app.domain.data.importer import canonical_content_hash
from app.infrastructure.storage import ParquetStore


class IFindIngestionError(ValueError):
    """Raised when an iFinD response cannot produce a complete publishable batch."""


class IFindIngestionService:
    """Fetch, normalize, archive, and publish one iFinD business-date batch.

    The client is deliberately duck-typed.  This keeps recordings and test transports
    independent of the concrete HTTP adapter while retaining the required fetch order.
    """

    PILOT_CODES = frozenset({"000001.SZ", "600000.SH", "000300.SH", "000001.SH"})
    SHANGHAI = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        client: Any,
        repository: Any,
        *,
        artifact_root: str | Path = "data",
        owner_id: UUID | None = None,
        mapping_version: str = "v1",
    ) -> None:
        self._client = client
        self._repository = repository
        self._store = ParquetStore(artifact_root)
        self._owner_id = owner_id or UUID(int=0)
        self._mapping_version = mapping_version

    def import_business_date(self, business_date: date | str, scope: str) -> dict[str, Any]:
        trade_date = self._as_date(business_date)
        if trade_date is None:
            raise ValueError("business_date must be an ISO date")
        if scope not in {"pilot", "full"}:
            raise ValueError("scope must be pilot or full")
        pulled_at = datetime.now(UTC)
        errors: list[str] = []

        # Calendar and the dated security universe are intentionally fetched first.
        calendar = self._fetch("trading_calendar", start_date=trade_date, end_date=trade_date)
        calendar_row = self._find_calendar(calendar, trade_date)
        if calendar_row is None or not self._truth(self._pick(calendar_row, "is_open", "isOpen")):
            errors.append("trading calendar is missing or closed")
        security_master = self._fetch(
            "security_master",
            start_date=trade_date,
            end_date=trade_date,
            extra={"business_date": trade_date.isoformat()},
        )
        master_by_code = {self._code(row): row for row in security_master if self._code(row)}
        codes = set(self.PILOT_CODES if scope == "pilot" else master_by_code)
        codes.update({"000300.SH", "000001.SH"})
        if not codes:
            errors.append("stock pool is empty")

        bars = self._fetch("daily_bars", codes=codes, start_date=trade_date, end_date=trade_date)
        factors = self._fetch(
            "adjustment_factors", codes=codes, start_date=trade_date, end_date=trade_date
        )
        statuses = self._fetch(
            "historical_status", codes=codes, start_date=trade_date, end_date=trade_date
        )
        industries = self._fetch(
            "industry_membership", codes=codes, start_date=trade_date, end_date=trade_date
        )

        factor_by_code = self._index(factors)
        bar_by_code = self._index(bars)
        status_by_code = self._index(statuses)
        industry_by_code = self._index(industries)
        missing = self._missing_requirements(
            codes, master_by_code, bar_by_code, factor_by_code, status_by_code, industry_by_code
        )
        errors.extend(missing)
        errors.extend(
            self._invalid_required_fields(
                codes, bar_by_code, factor_by_code, master_by_code, industry_by_code
            )
        )

        rows: list[dict[str, Any]] = []
        for code in sorted(codes):
            bar = bar_by_code.get(code)
            factor = factor_by_code.get(code)
            master = master_by_code.get(code, {})
            status = status_by_code.get(code, {})
            if bar is None or factor is None:
                continue
            factor_value = self._pick(factor, "adjust_factor", "adjustment_factor", "af")
            raw = {name: self._pick(bar, name) for name in ("open", "high", "low", "close")}
            multiplier = self._decimal(factor_value)
            if multiplier is None:
                continue
            raw_open = self._decimal(raw["open"])
            raw_high = self._decimal(raw["high"])
            raw_low = self._decimal(raw["low"])
            raw_close = self._decimal(raw["close"])
            if None in {raw_open, raw_high, raw_low, raw_close}:
                continue
            assert raw_open is not None and raw_high is not None
            assert raw_low is not None and raw_close is not None
            adjusted_open = self._decimal(self._pick(bar, "adjusted_open")) or raw_open * multiplier
            adjusted_high = self._decimal(self._pick(bar, "adjusted_high")) or raw_high * multiplier
            adjusted_low = self._decimal(self._pick(bar, "adjusted_low")) or raw_low * multiplier
            adjusted_close = (
                self._decimal(self._pick(bar, "adjusted_close")) or raw_close * multiplier
            )
            rows.append(
                {
                    "symbol": code,
                    "trade_date": trade_date,
                    "raw_open": raw_open,
                    "raw_high": raw_high,
                    "raw_low": raw_low,
                    "raw_close": raw_close,
                    "adjusted_open": adjusted_open,
                    "adjusted_high": adjusted_high,
                    "adjusted_low": adjusted_low,
                    "adjusted_close": adjusted_close,
                    "volume": self._decimal(self._pick(bar, "volume")),
                    "amount": self._decimal(self._pick(bar, "amount")),
                    "adjust_factor": multiplier,
                    "available_at": self._available_at(trade_date),
                    "information_cutoff_at": self._cutoff_at(trade_date),
                    "open_limit_up": self._explicit_bool(bar, "open_limit_up", "openLimitUp"),
                    "open_limit_down": self._explicit_bool(bar, "open_limit_down", "openLimitDown"),
                    "close_limit_up": self._explicit_bool(bar, "close_limit_up", "closeLimitUp"),
                    "close_limit_down": self._explicit_bool(
                        bar, "close_limit_down", "closeLimitDown"
                    ),
                    "limit_up": self._explicit_bool(bar, "close_limit_up", "closeLimitUp"),
                    "limit_down": self._explicit_bool(bar, "close_limit_down", "closeLimitDown"),
                    "is_st": self._truth(self._pick(status, "is_st", "st_flag", "isST")),
                    "is_suspended": self._truth(
                        self._pick(status, "is_suspended", "suspended", "isSuspended")
                    ),
                    "is_delist_period": self._truth(
                        self._pick(
                            status,
                            "is_delist_period",
                            "delisting_arrangement",
                            "isDelistingArrange",
                        )
                    ),
                    "listed_at": self._as_date(self._pick(master, "listed_at", "listedDate")),
                    "delisted_at": self._as_date(self._pick(master, "delisted_at", "delistedDate")),
                    "board": self._pick(master, "board"),
                }
            )

        standardized = self._store.write_records(
            "ifind_daily_bars",
            [self._jsonable(row) for row in rows],
            layer="standardized",
            version=f"v-{canonical_content_hash(rows)[:16] if rows else sha256(trade_date.isoformat().encode()).hexdigest()[:16]}",
        )
        payload = {
            "source_name": "ifind_http",
            "data_type": "DAILY_BAR",
            "date_from": trade_date.isoformat(),
            "date_to": trade_date.isoformat(),
            "available_at": self._available_at(trade_date),
            "information_cutoff_at": self._cutoff_at(trade_date),
            "file_location": str(standardized.path),
            "version": standardized.version,
            "content_hash": canonical_content_hash(rows),
            "file_hash": standardized.sha256,
            "actual_pulled_at": pulled_at.isoformat(),
            "mapping_version": self._mapping_version,
            "adjustment_convention": "RAW_TIMES_ADJUST_FACTOR",
            "field_convention": "DAILY_OHLCV_OPEN_CLOSE_LIMIT_V2",
            "provenance": {
                "standardized_manifest_path": str(standardized.manifest_path),
                "standardized_manifest_hash": standardized.sha256,
                "raw_manifest_paths": self._raw_manifest_paths(),
                "raw_manifests": self._raw_manifest_details(),
                "mapping_version": self._mapping_version,
            },
            "ifind_metadata": {
                "calendar": calendar,
                "security_master": security_master,
                "statuses": statuses,
                "industries": industries,
                "actual_pulled_at": pulled_at.isoformat(),
            },
            "quality_errors": errors,
        }
        if errors:
            rows = []
        if hasattr(self._repository, "import_ifind_batch"):
            result = self._repository.import_ifind_batch(self._owner_id, payload, rows)
        else:
            result = self._repository.import_daily_bars(self._owner_id, payload, rows)
        return {
            **result,
            "standardized_manifest": standardized.as_dict(),
            "pulled_at": pulled_at.isoformat(),
        }

    def _fetch(
        self, dataset: str, codes: Iterable[str] | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        try:
            if hasattr(self._client, "fetch_dataset"):
                result = self._client.fetch_dataset(dataset, codes, **kwargs)
            else:
                method = getattr(self._client, f"fetch_{dataset}")
                result = method(codes, **kwargs) if codes is not None else method(**kwargs)
        except DependencyError:
            # Preserve retry classification for timeout, 429/5xx, and auth failures.
            raise
        except Exception as exc:
            # Keep dependency failures visible to the worker and do not publish a partial batch.
            raise IFindIngestionError(f"iFinD {dataset} fetch failed") from exc
        return [dict(row) for row in (result or []) if isinstance(row, Mapping)]

    @classmethod
    def _missing_requirements(
        cls, codes: set[str], *indexes: Mapping[str, Mapping[str, Any]]
    ) -> list[str]:
        labels = (
            "security master",
            "daily bars",
            "adjustment factors",
            "historical status",
            "industry membership",
        )
        return [
            f"{labels[i]} missing for {sorted(codes - set(index))}"
            for i, index in enumerate(indexes)
            if codes - set(index)
        ]

    @classmethod
    def _invalid_required_fields(
        cls,
        codes: set[str],
        bars: Mapping[str, Mapping[str, Any]],
        factors: Mapping[str, Mapping[str, Any]],
        masters: Mapping[str, Mapping[str, Any]],
        industries: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        errors: list[str] = []
        for code in sorted(codes):
            bar = bars.get(code, {})
            for field in ("open", "high", "low", "close", "volume", "amount"):
                if cls._pick(bar, field) is None:
                    errors.append(f"daily bars field {field} missing for {code}")
            for normalized, provider in (
                ("open_limit_up", "openLimitUp"),
                ("open_limit_down", "openLimitDown"),
                ("close_limit_up", "closeLimitUp"),
                ("close_limit_down", "closeLimitDown"),
            ):
                if cls._pick(bar, normalized, provider) is None:
                    errors.append(f"price limit evidence {normalized} missing for {code}")
            if cls._pick(factors.get(code, {}), "adjust_factor", "adjustment_factor", "af") is None:
                errors.append(f"adjustment factor missing for {code}")
            master = masters.get(code, {})
            if cls._as_date(cls._pick(master, "listed_at", "listedDate")) is None:
                errors.append(f"listing date missing for {code}")
            if cls._pick(master, "board") is None:
                errors.append(f"board missing for {code}")
            industry = industries.get(code, {})
            if cls._pick(industry, "industry_code", "industryCode") is None:
                errors.append(f"industry code missing for {code}")
            if cls._as_date(cls._pick(industry, "valid_from", "inDate")) is None:
                errors.append(f"industry start date missing for {code}")
        return errors

    @classmethod
    def _index(cls, rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        return {code: dict(row) for row in rows if (code := cls._code(row))}

    @classmethod
    def _code(cls, row: Mapping[str, Any]) -> str:
        return str(cls._pick(row, "symbol", "security_code", "thscode") or "")

    @staticmethod
    def _pick(row: Mapping[str, Any], *names: str) -> Any:
        for name in names:
            if name in row and row[name] not in (None, ""):
                return row[name]
        return None

    @classmethod
    def _find_calendar(
        cls, rows: Iterable[Mapping[str, Any]], trade_date: date
    ) -> Mapping[str, Any] | None:
        for row in rows:
            value = cls._as_date(cls._pick(row, "trade_date", "business_date", "tradeDate"))
            if value == trade_date:
                return row
        return next(iter(rows), None)

    @staticmethod
    def _as_date(value: date | str | Any) -> date | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def _truth(cls, value: Any) -> bool:
        return value is True or str(value).lower() in {"true", "1", "yes", "y", "是"}

    @classmethod
    def _explicit_bool(cls, row: Mapping[str, Any], *names: str) -> bool | None:
        value = cls._pick(row, *names)
        return None if value is None else cls._truth(value)

    @classmethod
    def _cutoff_at(cls, trade_date: date) -> datetime:
        return cls._available_at(trade_date)

    @classmethod
    def _available_at(cls, trade_date: date) -> datetime:
        return datetime.combine(trade_date, time(18, 30), cls.SHANGHAI)

    @staticmethod
    def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: (
                value.isoformat()
                if isinstance(value, (datetime, date))
                else str(value)
                if isinstance(value, Decimal)
                else value
            )
            for key, value in row.items()
        }

    def _raw_manifest_paths(self) -> list[str]:
        archive = getattr(self._client, "_raw_archive", None)
        root = getattr(getattr(archive, "_store", None), "root", None)
        if root is None:
            return []
        return [str(path) for path in sorted(Path(root).glob("raw/*/*/manifest.json"))]

    def _raw_manifest_details(self) -> list[dict[str, str]]:
        details: list[dict[str, str]] = []
        for path in self._raw_manifest_paths():
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            details.append({"path": path, "sha256": str(payload.get("sha256", ""))})
        return details


__all__ = ["IFindIngestionError", "IFindIngestionService"]
