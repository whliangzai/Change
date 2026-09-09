"""Read-only AKShare cross-checking; never used to publish or repair prices."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.infrastructure.providers import RawResponseArchive


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if "." in code:
        return code
    if len(code) == 6:
        return f"{code}.{'SH' if code.startswith(('5', '6', '9')) else 'SZ'}"
    return code


def _date(value: Any) -> str:
    return str(value)[:10]


def compare_ohlcv(
    primary_rows: Iterable[Mapping[str, Any]],
    validation_rows: Iterable[Mapping[str, Any]],
    *,
    price_tolerance: Decimal = Decimal("0"),
    volume_tolerance: Decimal = Decimal("0"),
    amount_tolerance: Decimal = Decimal("0"),
) -> list[dict[str, Any]]:
    """Return evidence-rich warnings; this function intentionally never raises."""
    secondary = {
        (
            _code(row.get("symbol") or row.get("ts_code") or row.get("code")),
            _date(row.get("trade_date") or row.get("date")),
        ): row
        for row in validation_rows
    }
    warnings: list[dict[str, Any]] = []
    fields = (
        ("open", price_tolerance),
        ("high", price_tolerance),
        ("low", price_tolerance),
        ("close", price_tolerance),
        ("volume", volume_tolerance),
        ("amount", amount_tolerance),
    )
    for row in primary_rows:
        symbol = _code(row.get("symbol") or row.get("ts_code"))
        trade_date = _date(row.get("trade_date"))
        other = secondary.get((symbol, trade_date))
        if other is None:
            warnings.append(
                {
                    "severity": "WARNING",
                    "kind": "AKSHARE_MISSING",
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "message": "AKShare validation row is unavailable",
                }
            )
            continue
        for field, tolerance in fields:
            primary, checked = _decimal(row.get(field)), _decimal(other.get(field))
            if primary is None or checked is None:
                warnings.append(
                    {
                        "severity": "WARNING",
                        "kind": "AKSHARE_FIELD_MISSING",
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "field": field,
                        "message": "AKShare validation field is missing",
                    }
                )
                continue
            error = (
                Decimal("0")
                if primary == checked
                else abs(primary - checked) / max(abs(primary), Decimal("1"))
            )
            if error > tolerance:
                warnings.append(
                    {
                        "severity": "WARNING",
                        "kind": "AKSHARE_DIFFERENCE",
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "field": field,
                        "primary_value": str(primary),
                        "validation_value": str(checked),
                        "relative_error": str(error),
                        "tolerance": str(tolerance),
                        "message": "Tushare and AKShare values differ",
                    }
                )
    return warnings


class AKShareValidationClient:
    """Small adapter intentionally restricted to calendar and unadjusted daily bars."""

    def __init__(
        self, *, raw_archive: RawResponseArchive | None = None, module: Any | None = None
    ) -> None:
        self._raw_archive = raw_archive
        self._module = module

    def _api(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            import akshare  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("AKShare is not installed") from exc
        return akshare

    def fetch_calendar(self, business_date: date) -> list[dict[str, Any]]:
        # AKShare exposes exchange dates through a calendar endpoint; keep failure non-blocking.
        api = self._api()
        frame = api.tool_trade_date_hist_sina()
        rows = [
            {"trade_date": _date(value), "is_open": True} for value in frame.iloc[:, 0].tolist()
        ]
        self._archive("trading_calendar", {"business_date": business_date.isoformat()}, rows)
        return rows

    def fetch_daily_bars(self, codes: Iterable[str], business_date: date) -> list[dict[str, Any]]:
        api = self._api()
        compact_date = business_date.strftime("%Y%m%d")
        rows: list[dict[str, Any]] = []
        for code in codes:
            symbol = _code(code)
            raw_code = symbol[:6]
            if symbol in {"000300.SH", "000001.SH"}:
                frame = api.stock_zh_index_daily_em(symbol=f"sh{raw_code}")
            else:
                frame = api.stock_zh_a_hist(
                    symbol=raw_code,
                    period="daily",
                    start_date=compact_date,
                    end_date=compact_date,
                    adjust="",
                )
            records = frame.to_dict("records")
            for item in records:
                item_date = _date(item.get("日期") or item.get("date"))
                if item_date != business_date.isoformat():
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": item_date,
                        "open": item.get("开盘") or item.get("open"),
                        "high": item.get("最高") or item.get("high"),
                        "low": item.get("最低") or item.get("low"),
                        "close": item.get("收盘") or item.get("close"),
                        "volume": item.get("成交量") or item.get("volume"),
                        "amount": item.get("成交额") or item.get("amount"),
                    }
                )
        self._archive(
            "daily_bars", {"business_date": business_date.isoformat(), "codes": sorted(codes)}, rows
        )
        return rows

    def _archive(self, dataset: str, request: Mapping[str, Any], response: Any) -> None:
        if self._raw_archive is not None:
            self._raw_archive.archive(
                "akshare", dataset, request, None, response, mapping_version="v1"
            )


__all__ = ["AKShareValidationClient", "compare_ohlcv"]
