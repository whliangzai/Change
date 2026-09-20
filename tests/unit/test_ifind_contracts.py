from dataclasses import fields

import pytest

from app.core.config import ConfigurationError, load_settings
from app.infrastructure.ifind.contracts import (
    DEFAULT_IFIND_CONTRACT,
    IFIND_CONTRACT_VERSION,
    IFindContractError,
    IFindMappingUnavailableError,
    missing_required_fields,
    validate_ifind_mapping,
    validate_ifind_payload,
)


def _settings(overrides: dict[str, str] | None = None):
    values = {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
    values.update(overrides or {})
    return load_settings(values)


def test_ifind_settings_have_safe_defaults_and_secret_is_not_repr() -> None:
    settings = _settings()

    assert settings.ifind_enabled is False
    assert settings.ifind_base_url == "https://quantapi.51ifind.com"
    assert settings.ifind_daily_run_at == "18:30"
    assert settings.ifind_max_codes_per_request == 200
    assert settings.ifind_max_concurrency == 2
    assert "ifind_refresh_token" not in repr(settings)


def test_ifind_enabled_requires_refresh_token_and_validates_limits() -> None:
    with pytest.raises(ConfigurationError, match="IFIND_REFRESH_TOKEN"):
        _settings({"IFIND_ENABLED": "true"})

    settings = _settings(
        {
            "IFIND_ENABLED": "1",
            "IFIND_REFRESH_TOKEN": "test-refresh-token",
            "IFIND_MAX_CODES_PER_REQUEST": "50",
            "IFIND_MAX_CONCURRENCY": "4",
        }
    )
    assert settings.ifind_enabled is True
    assert settings.ifind_refresh_token == "test-refresh-token"
    assert settings.ifind_max_codes_per_request == 50
    assert settings.ifind_max_concurrency == 4

    with pytest.raises(ConfigurationError, match="IFIND_MAX_CODES_PER_REQUEST"):
        _settings({"IFIND_MAX_CODES_PER_REQUEST": "201"})
    with pytest.raises(ConfigurationError, match="IFIND_MAX_CONCURRENCY"):
        _settings({"IFIND_MAX_CONCURRENCY": "0"})
    with pytest.raises(ConfigurationError, match="IFIND_DAILY_RUN_AT"):
        _settings({"IFIND_DAILY_RUN_AT": "8:30"})
    with pytest.raises(ConfigurationError, match="quantapi.51ifind.com"):
        _settings({"IFIND_BASE_URL": "https://example.test"})


def test_contract_is_versioned_and_covers_required_historical_datasets() -> None:
    assert IFIND_CONTRACT_VERSION == "v1"
    expected = {
        "daily_bars",
        "adjustment_factors",
        "trading_calendar",
        "security_master",
        "historical_status",
        "industry_membership",
    }
    assert set(DEFAULT_IFIND_CONTRACT.datasets) == expected
    assert all(dataset.historical for dataset in DEFAULT_IFIND_CONTRACT.datasets.values())


def test_mapping_validation_blocks_missing_required_provider_fields() -> None:
    available = {
        name: dataset.provider_fields for name, dataset in DEFAULT_IFIND_CONTRACT.datasets.items()
    }
    available["historical_status"] = available["historical_status"] - {"isST"}

    missing = missing_required_fields(available)
    assert missing["historical_status"] == frozenset({"isST"})
    with pytest.raises(IFindMappingUnavailableError, match="historical_status"):
        validate_ifind_mapping(available)


def test_payload_validation_accepts_normalized_or_provider_names() -> None:
    dataset = DEFAULT_IFIND_CONTRACT.dataset("daily_bars")
    normalized = {field: field for field in dataset.required_fields}
    validate_ifind_payload("daily_bars", normalized)

    with pytest.raises(IFindContractError, match="daily_bars"):
        validate_ifind_payload("daily_bars", {"tradeDate": "2025-01-02"})


def test_contract_dataclasses_are_immutable() -> None:
    contract = DEFAULT_IFIND_CONTRACT.dataset("daily_bars")
    with pytest.raises((AttributeError, TypeError)):
        contract.endpoint = "https://example.invalid"  # type: ignore[misc]
    with pytest.raises(TypeError):
        contract.fields["open"] = "other"  # type: ignore[index]
    assert all(field.name for field in fields(contract))
