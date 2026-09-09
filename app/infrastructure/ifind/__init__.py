"""iFinD HTTP integration contracts and adapters."""

from app.infrastructure.ifind.contracts import (
    DEFAULT_IFIND_CONTRACT,
    IFIND_CONTRACT_VERSION,
    IFindContractError,
    IFindFieldContract,
    IFindMappingUnavailableError,
    missing_required_fields,
    validate_ifind_mapping,
    validate_ifind_payload,
)

try:  # Keep contract-only tooling usable before HTTP dependencies are installed.
    from app.infrastructure.ifind.http_client import IFindDataError, IFindHttpClient
    from app.infrastructure.ifind.raw_archive import IFindRawArchive
except ModuleNotFoundError as exc:
    if exc.name != "httpx":
        raise
    IFindDataError = None  # type: ignore[assignment,misc]
    IFindHttpClient = None  # type: ignore[assignment,misc]
    IFindRawArchive = None  # type: ignore[assignment,misc]

__all__ = [
    "DEFAULT_IFIND_CONTRACT",
    "IFIND_CONTRACT_VERSION",
    "IFindContractError",
    "IFindFieldContract",
    "IFindMappingUnavailableError",
    "missing_required_fields",
    "validate_ifind_mapping",
    "validate_ifind_payload",
    "IFindDataError",
    "IFindHttpClient",
    "IFindRawArchive",
]
