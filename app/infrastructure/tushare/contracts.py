"""Versioned field contract for the publishable Tushare feed."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

TUSHARE_CONTRACT_VERSION = "v1"


class TushareContractError(ValueError):
    pass


class TushareMappingUnavailableError(TushareContractError):
    def __init__(self, missing: Mapping[str, Iterable[str]]) -> None:
        self.missing = {name: frozenset(values) for name, values in missing.items()}
        detail = "; ".join(
            f"{name}: {', '.join(sorted(values))}" for name, values in self.missing.items()
        )
        super().__init__(f"Tushare field mapping is unavailable ({detail})")


@dataclass(frozen=True, slots=True)
class TushareDatasetContract:
    name: str
    api_name: str
    fields: Mapping[str, str]
    historical: bool = True
    allow_empty: bool = False

    @property
    def provider_fields(self) -> frozenset[str]:
        return frozenset(self.fields.values())


@dataclass(frozen=True, slots=True)
class TushareFieldContract:
    version: str
    datasets: Mapping[str, TushareDatasetContract]

    def dataset(self, name: str) -> TushareDatasetContract:
        try:
            return self.datasets[name]
        except KeyError as exc:
            raise TushareContractError(f"unknown Tushare dataset: {name}") from exc

    def validate_payload(self, dataset: str, payload: Mapping[str, Any]) -> None:
        contract = self.dataset(dataset)
        missing = [
            normalized
            for normalized, provider in contract.fields.items()
            if normalized not in payload and provider not in payload
        ]
        if missing:
            raise TushareContractError(
                f"Tushare {dataset} response is missing required fields: {', '.join(sorted(missing))}"
            )


def _dataset(
    name: str, api_name: str, *, allow_empty: bool = False, **fields: str
) -> TushareDatasetContract:
    return TushareDatasetContract(name, api_name, MappingProxyType(fields), allow_empty=allow_empty)


DEFAULT_TUSHARE_CONTRACT = TushareFieldContract(
    version=TUSHARE_CONTRACT_VERSION,
    datasets=MappingProxyType(
        {
            "trading_calendar": _dataset(
                "trading_calendar", "trade_cal", trade_date="cal_date", is_open="is_open"
            ),
            "security_master": _dataset(
                "security_master",
                "stock_basic",
                # A status-specific stock_basic request, especially ``P``, may
                # legitimately have no rows. The ingestion service separately
                # fails closed when no eligible full-scope securities remain.
                allow_empty=True,
                security_code="ts_code",
                listed_at="list_date",
                delisted_at="delist_date",
                board="market",
            ),
            "index_master": _dataset(
                "index_master",
                "index_basic",
                security_code="ts_code",
                listed_at="list_date",
                board="market",
            ),
            "daily_bars": _dataset(
                "daily_bars",
                "daily",
                security_code="ts_code",
                trade_date="trade_date",
                open="open",
                high="high",
                low="low",
                close="close",
                volume="vol",
                amount="amount",
            ),
            "index_daily_bars": _dataset(
                "index_daily_bars",
                "index_daily",
                security_code="ts_code",
                trade_date="trade_date",
                open="open",
                high="high",
                low="low",
                close="close",
                volume="vol",
                amount="amount",
            ),
            "adjustment_factors": _dataset(
                "adjustment_factors",
                "adj_factor",
                security_code="ts_code",
                trade_date="trade_date",
                adjustment_factor="adj_factor",
            ),
            # stock_st is the dated authoritative ST list. Absent requested codes are non-ST.
            "historical_status": _dataset(
                "historical_status",
                "stock_st",
                allow_empty=True,
                security_code="ts_code",
                trade_date="trade_date",
            ),
            "suspensions": _dataset(
                "suspensions",
                "suspend_d",
                allow_empty=True,
                security_code="ts_code",
                trade_date="trade_date",
                suspend_type="suspend_type",
            ),
            "industry_membership": _dataset(
                "industry_membership",
                "index_member_all",
                security_code="ts_code",
                industry_code="l3_code",
                valid_from="in_date",
                valid_to="out_date",
            ),
        }
    ),
)


def validate_tushare_mapping(available: Mapping[str, Iterable[str]]) -> None:
    missing: dict[str, frozenset[str]] = {}
    for name, dataset in DEFAULT_TUSHARE_CONTRACT.datasets.items():
        fields = frozenset(available.get(name, ()))
        absent = frozenset(field for field in dataset.provider_fields if field not in fields)
        if absent:
            missing[name] = absent
    if missing:
        raise TushareMappingUnavailableError(missing)


__all__ = [
    "DEFAULT_TUSHARE_CONTRACT",
    "TUSHARE_CONTRACT_VERSION",
    "TushareContractError",
    "TushareDatasetContract",
    "TushareFieldContract",
    "TushareMappingUnavailableError",
    "validate_tushare_mapping",
]
