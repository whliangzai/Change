"""Publishable Tushare ingestion with non-blocking AKShare evidence."""

from __future__ import annotations

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
from app.infrastructure.akshare import compare_ohlcv
from app.infrastructure.storage import ParquetStore


class TushareIngestionError(ValueError):
    """A Tushare batch cannot be published because required evidence is incomplete."""


class TushareIngestionService:
    PILOT_CODES = frozenset({"000001.SZ", "600000.SH", "000300.SH", "000001.SH"})
    INDEX_CODES = frozenset({"000300.SH", "000001.SH"})
    STOCK_LIST_STATUSES = ("L", "D", "P")
    SHANGHAI = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        client: Any,
        repository: Any,
        *,
        validation_client: Any | None = None,
        artifact_root: str | Path = "data",
        owner_id: UUID | None = None,
        mapping_version: str = "v1",
        price_tolerance: Decimal = Decimal("0"),
        volume_tolerance: Decimal = Decimal("0"),
        amount_tolerance: Decimal = Decimal("0"),
        pilot_codes: Iterable[str] | None = None,
    ) -> None:
        self._client, self._repository, self._validation_client = (
            client,
            repository,
            validation_client,
        )
        self._store, self._owner_id, self._mapping_version = (
            ParquetStore(artifact_root),
            owner_id or UUID(int=0),
            mapping_version,
        )
        self._price_tolerance, self._volume_tolerance, self._amount_tolerance = (
            price_tolerance,
            volume_tolerance,
            amount_tolerance,
        )
        self._pilot_codes = frozenset(code.upper() for code in (pilot_codes or self.PILOT_CODES))

    def import_business_date(self, business_date: date | str, scope: str) -> dict[str, Any]:
        trade_date = self._as_date(business_date)
        if trade_date is None or scope not in {"pilot", "full"}:
            raise ValueError("business_date must be ISO date and scope must be pilot or full")
        pulled_at, errors = datetime.now(UTC), []
        preflight = self._preflight()
        calendar = self._fetch("trading_calendar", start_date=trade_date, end_date=trade_date)
        calendar_row = self._find_calendar(calendar, trade_date)
        if calendar_row is None or not self._truth(self._pick(calendar_row, "is_open", "isOpen")):
            errors.append("trading calendar is missing or closed")
        stock_list_statuses = self.STOCK_LIST_STATUSES if scope == "full" else ("L",)
        stock_master = [
            row
            for list_status in stock_list_statuses
            for row in self._fetch("security_master", extra={"list_status": list_status})
        ]
        index_master = self._fetch("index_master", self.INDEX_CODES)
        master = stock_master + index_master
        master_by_code = self._index(master)
        stock_master_by_code = self._index(stock_master)
        stock_codes = (
            set(self._pilot_codes) - self.INDEX_CODES
            if scope == "pilot"
            else self._listed_stock_codes(stock_master_by_code, trade_date)
        )
        if not stock_codes:
            errors.append("stock pool is empty")
        codes = stock_codes | self.INDEX_CODES
        index_codes = self.INDEX_CODES
        bars = self._fetch("daily_bars", stock_codes, start_date=trade_date, end_date=trade_date)
        bars += self._fetch(
            "index_daily_bars", index_codes, start_date=trade_date, end_date=trade_date
        )
        factors = self._fetch(
            "adjustment_factors", stock_codes, start_date=trade_date, end_date=trade_date
        )
        st_rows = self._fetch(
            "historical_status", stock_codes, start_date=trade_date, end_date=trade_date
        )
        suspensions = self._fetch(
            "suspensions", stock_codes, start_date=trade_date, end_date=trade_date
        )
        industries = self._fetch(
            "industry_membership", stock_codes, start_date=trade_date, end_date=trade_date
        )
        bar_by_code, factor_by_code, industry_by_code = (
            self._index(rows) for rows in (bars, factors, industries)
        )
        st_codes = {self._code(row) for row in st_rows}
        suspended_codes = {
            self._code(row)
            for row in suspensions
            if str(self._pick(row, "suspend_type")).upper() == "S"
        }
        stock_codes_with_bars = stock_codes & set(bar_by_code)
        suspended_without_bars = suspended_codes - stock_codes_with_bars
        daily_stock_codes = stock_codes - suspended_without_bars
        daily_codes = daily_stock_codes | index_codes
        status_by_code = {
            code: {
                "ts_code": code,
                "trade_date": trade_date,
                "is_st": code in st_codes,
                "is_suspended": code in suspended_codes,
                "is_delist_period": False,
            }
            for code in stock_codes
        }
        errors.extend(self._missing("security master", codes, master_by_code))
        errors.extend(self._missing("daily bars", daily_codes, bar_by_code))
        errors.extend(self._missing("adjustment factors", daily_stock_codes, factor_by_code))
        errors.extend(self._missing("industry membership", stock_codes, industry_by_code))
        errors.extend(
            self._invalid_required_fields(
                codes,
                daily_codes,
                daily_stock_codes,
                stock_codes,
                bar_by_code,
                factor_by_code,
                master_by_code,
                industry_by_code,
            )
        )
        rows = self._standardize(
            daily_codes, trade_date, bar_by_code, factor_by_code, status_by_code, master_by_code
        )
        manifest = self._store.write_records(
            "tushare_daily_bars",
            [self._jsonable(row) for row in rows],
            layer="standardized",
            version=f"v-{canonical_content_hash(rows)[:16] if rows else sha256(trade_date.isoformat().encode()).hexdigest()[:16]}",
        )
        warnings, akshare_manifests = self._validate_with_akshare(rows, trade_date, codes)
        payload = {
            "source_name": "tushare_http",
            "data_type": "DAILY_BAR",
            "date_from": trade_date.isoformat(),
            "date_to": trade_date.isoformat(),
            "available_at": self._available_at(trade_date),
            "information_cutoff_at": self._cutoff_at(trade_date),
            "file_location": str(manifest.path),
            "version": manifest.version,
            "content_hash": canonical_content_hash(rows),
            "file_hash": manifest.sha256,
            "actual_pulled_at": pulled_at.isoformat(),
            "mapping_version": self._mapping_version,
            "provider_metadata": {
                "calendar": calendar,
                "security_master": master,
                "security_master_list_statuses": list(stock_list_statuses),
                "statuses": list(status_by_code.values()),
                "industries": industries,
                "actual_pulled_at": pulled_at.isoformat(),
            },
            "quality_errors": errors,
            "quality_warnings": warnings,
            "license_note": "Tushare Pro API",
            "provenance": {
                "mapping_version": self._mapping_version,
                "permission_preflight": preflight,
                "standardized_manifest_path": str(manifest.manifest_path),
                "standardized_manifest_hash": manifest.sha256,
                "raw_manifest_paths": self._raw_manifests(self._client),
                "akshare_raw_manifest_paths": akshare_manifests,
                "akshare_validation": {
                    "source": "akshare",
                    "warning_count": len(warnings),
                    "price_relative_tolerance": str(self._price_tolerance),
                    "volume_relative_tolerance": str(self._volume_tolerance),
                    "amount_relative_tolerance": str(self._amount_tolerance),
                },
            },
        }
        result = self._repository.import_provider_batch(
            self._owner_id, payload, [] if errors else rows
        )
        return {
            **result,
            "standardized_manifest": manifest.as_dict(),
            "pulled_at": pulled_at.isoformat(),
        }

    def _preflight(self) -> dict[str, Any]:
        try:
            report = getattr(self._client, "preflight", lambda: {"status": "not_supported"})()
        except DependencyError:
            raise
        except Exception as exc:
            raise TushareIngestionError("Tushare permission preflight failed") from exc
        return dict(report) if isinstance(report, Mapping) else {"status": "completed"}

    def _validate_with_akshare(
        self, rows: list[dict[str, Any]], trade_date: date, codes: set[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if self._validation_client is None:
            return [
                {
                    "severity": "WARNING",
                    "kind": "AKSHARE_DISABLED",
                    "message": "AKShare validation is disabled",
                }
            ], []
        try:
            calendar = self._validation_client.fetch_calendar(trade_date)
            secondary = self._validation_client.fetch_daily_bars(sorted(codes), trade_date)
            warnings = compare_ohlcv(
                rows,
                secondary,
                price_tolerance=self._price_tolerance,
                volume_tolerance=self._volume_tolerance,
                amount_tolerance=self._amount_tolerance,
            )
            calendar_dates = {
                self._as_date(self._pick(item, "trade_date", "date")) for item in calendar
            }
            if trade_date not in calendar_dates:
                warnings.append(
                    {
                        "severity": "WARNING",
                        "kind": "AKSHARE_CALENDAR_MISSING",
                        "trade_date": trade_date.isoformat(),
                        "message": "AKShare calendar does not include the business date",
                    }
                )
        except Exception as exc:
            warnings = [
                {
                    "severity": "WARNING",
                    "kind": "AKSHARE_UNAVAILABLE",
                    "message": f"AKShare validation failed: {type(exc).__name__}",
                }
            ]
        return warnings, self._raw_manifests(self._validation_client)

    def _fetch(
        self, dataset: str, codes: Iterable[str] | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        try:
            result = (
                self._client.fetch_dataset(dataset, codes, **kwargs)
                if hasattr(self._client, "fetch_dataset")
                else getattr(self._client, f"fetch_{dataset}")(codes, **kwargs)
            )
        except DependencyError:
            raise
        except Exception as exc:
            detail = str(exc).replace("\r", " ").replace("\n", " ").strip()[:240]
            raise TushareIngestionError(
                f"Tushare {dataset} fetch failed ({type(exc).__name__}): {detail}"
            ) from exc
        return [dict(row) for row in (result or []) if isinstance(row, Mapping)]

    @classmethod
    def _standardize(
        cls,
        codes: set[str],
        trade_date: date,
        bars: Mapping[str, Mapping[str, Any]],
        factors: Mapping[str, Mapping[str, Any]],
        statuses: Mapping[str, Mapping[str, Any]],
        masters: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        rows = []
        for code in sorted(codes):
            bar, factor, status, master = (
                bars.get(code, {}),
                factors.get(code, {}),
                statuses.get(code, {}),
                masters.get(code, {}),
            )
            values = {
                field: cls._decimal(cls._pick(bar, field))
                for field in ("open", "high", "low", "close")
            }
            multiplier = cls._decimal(cls._pick(factor, "adjust_factor", "adj_factor"))
            if code in cls.INDEX_CODES:
                multiplier = Decimal("1")
            if multiplier is None or any(value is None for value in values.values()):
                continue
            raw_open = values["open"]
            raw_high = values["high"]
            raw_low = values["low"]
            raw_close = values["close"]
            assert (
                raw_open is not None
                and raw_high is not None
                and raw_low is not None
                and raw_close is not None
            )
            rows.append(
                {
                    "symbol": code,
                    "trade_date": trade_date,
                    "raw_open": raw_open,
                    "raw_high": raw_high,
                    "raw_low": raw_low,
                    "raw_close": raw_close,
                    "adjusted_open": raw_open * multiplier,
                    "adjusted_high": raw_high * multiplier,
                    "adjusted_low": raw_low * multiplier,
                    "adjusted_close": raw_close * multiplier,
                    "open": raw_open,
                    "high": raw_high,
                    "low": raw_low,
                    "close": raw_close,
                    "volume": cls._decimal(cls._pick(bar, "volume", "vol")),
                    "amount": cls._decimal(cls._pick(bar, "amount")),
                    "adjust_factor": multiplier,
                    "available_at": cls._available_at(trade_date),
                    "information_cutoff_at": cls._cutoff_at(trade_date),
                    "is_st": cls._truth(cls._pick(status, "is_st", "st_flag")),
                    "is_suspended": cls._truth(cls._pick(status, "is_suspended", "suspended")),
                    "is_delist_period": cls._truth(
                        cls._pick(status, "is_delist_period", "delisting_arrangement")
                    ),
                    "listed_at": cls._as_date(cls._pick(master, "listed_at", "list_date")),
                    "delisted_at": cls._as_date(cls._pick(master, "delisted_at", "delist_date")),
                    "board": cls._pick(master, "board", "market"),
                }
            )
        return rows

    @classmethod
    def _listed_stock_codes(
        cls, masters: Mapping[str, Mapping[str, Any]], trade_date: date
    ) -> set[str]:
        """Return securities that existed on the requested business date.

        ``stock_basic`` defaults to ``list_status=L``.  The full scope also reads
        ``D`` records so that a delisted security remains available for dates before
        its delisting, without leaking into dates on or after that event.
        """
        codes: set[str] = set()
        for code, master in masters.items():
            listed_at = cls._as_date(cls._pick(master, "listed_at", "list_date"))
            delisted_at = cls._as_date(cls._pick(master, "delisted_at", "delist_date"))
            if (listed_at is None or listed_at <= trade_date) and (
                delisted_at is None or trade_date < delisted_at
            ):
                codes.add(code)
        return codes - cls.INDEX_CODES

    @classmethod
    def _missing(
        cls, label: str, codes: set[str], values: Mapping[str, Mapping[str, Any]]
    ) -> list[str]:
        missing = codes - set(values)
        return [f"{label} missing for {sorted(missing)}"] if missing else []

    @classmethod
    def _invalid_required_fields(
        cls,
        all_codes: set[str],
        daily_codes: set[str],
        factor_codes: set[str],
        industry_codes: set[str],
        bars: Mapping[str, Mapping[str, Any]],
        factors: Mapping[str, Mapping[str, Any]],
        masters: Mapping[str, Mapping[str, Any]],
        industries: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        errors = []
        for code in sorted(daily_codes):
            for field in ("open", "high", "low", "close", "volume", "amount"):
                if (
                    cls._pick(bars.get(code, {}), field, "vol" if field == "volume" else field)
                    is None
                ):
                    errors.append(f"daily bars field {field} missing for {code}")
        for code in sorted(factor_codes):
            if cls._pick(factors.get(code, {}), "adjust_factor", "adj_factor") is None:
                errors.append(f"adjustment factor missing for {code}")
        for code in sorted(all_codes):
            if cls._as_date(cls._pick(masters.get(code, {}), "listed_at", "list_date")) is None:
                errors.append(f"listing date missing for {code}")
            if cls._pick(masters.get(code, {}), "board", "market") is None:
                errors.append(f"board missing for {code}")
        for code in sorted(industry_codes):
            if cls._pick(industries.get(code, {}), "industry_code", "l3_code") is None:
                errors.append(f"industry code missing for {code}")
            if cls._as_date(cls._pick(industries.get(code, {}), "valid_from", "in_date")) is None:
                errors.append(f"industry start date missing for {code}")
        return errors

    @classmethod
    def _index(cls, rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        return {code: dict(row) for row in rows if (code := cls._code(row))}

    @classmethod
    def _code(cls, row: Mapping[str, Any]) -> str:
        return str(cls._pick(row, "ts_code", "security_code", "symbol", "con_code") or "").upper()

    @staticmethod
    def _pick(row: Mapping[str, Any], *names: str) -> Any:
        return next((row[name] for name in names if row.get(name) not in (None, "")), None)

    @classmethod
    def _find_calendar(
        cls, rows: Iterable[Mapping[str, Any]], trade_date: date
    ) -> Mapping[str, Any] | None:
        return next(
            (
                row
                for row in rows
                if cls._as_date(cls._pick(row, "trade_date", "cal_date", "business_date"))
                == trade_date
            ),
            next(iter(rows), None),
        )

    @staticmethod
    def _as_date(value: Any) -> date | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value).replace("-", "")[:8])
        except ValueError:
            return None

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            return None if value in (None, "") else Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _truth(value: Any) -> bool:
        return value is True or str(value).lower() in {"true", "1", "yes", "y", "是"}

    @classmethod
    def _cutoff_at(cls, value: date) -> datetime:
        return datetime.combine(value, time(15), cls.SHANGHAI)

    @classmethod
    def _available_at(cls, value: date) -> datetime:
        return datetime.combine(value, time(18, 30), cls.SHANGHAI)

    @staticmethod
    def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value.isoformat()
            if isinstance(value, (datetime, date))
            else str(value)
            if isinstance(value, Decimal)
            else value
            for key, value in row.items()
        }

    @staticmethod
    def _raw_manifests(client: Any) -> list[str]:
        root = getattr(getattr(getattr(client, "_raw_archive", None), "_store", None), "root", None)
        return (
            [str(path) for path in sorted(Path(root).glob("raw/**/manifest.json"))]
            if root is not None
            else []
        )


__all__ = ["TushareIngestionError", "TushareIngestionService"]
