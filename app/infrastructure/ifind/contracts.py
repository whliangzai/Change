"""Versioned field contracts for the iFinD HTTP provider.

The contract deliberately names normalized fields and their provider-side names.  A
mapping validation failure is a hard dependency failure: importing a partial history
would make subsequent reports and backtests non-reproducible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

IFIND_CONTRACT_VERSION = "v1"


class IFindContractError(ValueError):
    """Base error for an unavailable or malformed iFinD response contract."""


class IFindMappingUnavailableError(IFindContractError):
    """Raised when one or more required provider fields are not available."""

    def __init__(self, missing: Mapping[str, Iterable[str]]) -> None:
        self.missing = {name: frozenset(fields) for name, fields in missing.items()}
        details = "; ".join(
            f"{dataset}: {', '.join(sorted(fields))}"
            for dataset, fields in sorted(self.missing.items())
        )
        super().__init__(f"iFinD field mapping is unavailable ({details})")


@dataclass(frozen=True, slots=True)
class IFindDatasetContract:
    """Mapping and required normalized fields for one provider dataset."""

    name: str
    endpoint: str
    fields: Mapping[str, str]
    historical: bool = True

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(self.fields)

    @property
    def provider_fields(self) -> frozenset[str]:
        return frozenset(self.fields.values())


@dataclass(frozen=True, slots=True)
class IFindFieldContract:
    """Versioned collection of all required iFinD datasets."""

    version: str
    datasets: Mapping[str, IFindDatasetContract]

    def dataset(self, name: str) -> IFindDatasetContract:
        try:
            return self.datasets[name]
        except KeyError as exc:
            raise IFindContractError(f"unknown iFinD dataset: {name}") from exc

    def missing_fields(
        self,
        available_fields: Mapping[str, Iterable[str]] | Iterable[str],
        datasets: Iterable[str] | None = None,
    ) -> dict[str, frozenset[str]]:
        requested = tuple(datasets or self.datasets)
        missing: dict[str, frozenset[str]] = {}
        for name in requested:
            contract = self.dataset(name)
            if isinstance(available_fields, Mapping):
                available = frozenset(available_fields.get(name, ()))
            else:
                available = frozenset(available_fields)
            # Accept either provider or normalized names per field (recorded
            # permission exports may legitimately contain a mixture of both).
            absent = {
                provider
                for normalized, provider in contract.fields.items()
                if normalized not in available and provider not in available
            }
            if absent:
                missing[name] = frozenset(absent)
        return missing

    def validate_mapping(
        self,
        available_fields: Mapping[str, Iterable[str]] | Iterable[str],
        datasets: Iterable[str] | None = None,
    ) -> None:
        missing = self.missing_fields(available_fields, datasets)
        if missing:
            raise IFindMappingUnavailableError(missing)

    def validate_payload(self, dataset: str, payload: Mapping[str, Any]) -> None:
        contract = self.dataset(dataset)
        missing = {
            normalized
            for normalized, provider in contract.fields.items()
            if normalized not in payload and provider not in payload
        }
        if missing:
            raise IFindContractError(
                f"iFinD {dataset} response is missing required fields: {', '.join(sorted(missing))}"
            )


def _dataset(name: str, endpoint: str, **fields: str) -> IFindDatasetContract:
    return IFindDatasetContract(name=name, endpoint=endpoint, fields=MappingProxyType(fields))


DEFAULT_IFIND_CONTRACT = IFindFieldContract(
    version=IFIND_CONTRACT_VERSION,
    datasets=MappingProxyType({
        "daily_bars": _dataset(
            "daily_bars",
            "/data/v1/high_frequency",
            business_date="tradeDate",
            security_code="thscode",
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
            amount="amount",
        ),
        "adjustment_factors": _dataset(
            "adjustment_factors",
            "/data/v1/ths_history",
            business_date="tradeDate",
            security_code="thscode",
            adjustment_factor="af",
        ),
        "trading_calendar": _dataset(
            "trading_calendar",
            "/data/v1/tradecalendar",
            business_date="tradeDate",
            is_open="isOpen",
        ),
        "security_master": _dataset(
            "security_master",
            "/data/v1/basic",
            security_code="thscode",
            security_name="securityName",
            listed_at="listedDate",
            delisted_at="delistedDate",
            board="board",
        ),
        "historical_status": _dataset(
            "historical_status",
            "/data/v1/special",
            business_date="tradeDate",
            security_code="thscode",
            st_flag="isST",
            suspended="isSuspended",
            delisting_arrangement="isDelistingArrange",
        ),
        "industry_membership": _dataset(
            "industry_membership",
            "/data/v1/sector",
            security_code="thscode",
            industry_code="industryCode",
            valid_from="inDate",
            valid_to="outDate",
        ),
    }),
)


def missing_required_fields(
    available_fields: Mapping[str, Iterable[str]] | Iterable[str],
    datasets: Iterable[str] | None = None,
    *,
    contract: IFindFieldContract = DEFAULT_IFIND_CONTRACT,
) -> dict[str, frozenset[str]]:
    """Return missing fields without mutating state; useful for permission reports."""

    return contract.missing_fields(available_fields, datasets)


def validate_ifind_mapping(
    available_fields: Mapping[str, Iterable[str]] | Iterable[str],
    datasets: Iterable[str] | None = None,
    *,
    contract: IFindFieldContract = DEFAULT_IFIND_CONTRACT,
) -> None:
    """Raise when required fields cannot be obtained from the configured account."""

    contract.validate_mapping(available_fields, datasets)


def validate_ifind_payload(
    dataset: str,
    payload: Mapping[str, Any],
    *,
    contract: IFindFieldContract = DEFAULT_IFIND_CONTRACT,
) -> None:
    """Raise when one supplier response omits a required field."""

    contract.validate_payload(dataset, payload)


__all__ = [
    "DEFAULT_IFIND_CONTRACT",
    "IFIND_CONTRACT_VERSION",
    "IFindContractError",
    "IFindDatasetContract",
    "IFindFieldContract",
    "IFindMappingUnavailableError",
    "missing_required_fields",
    "validate_ifind_mapping",
    "validate_ifind_payload",
]
