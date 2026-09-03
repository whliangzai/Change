from decimal import Decimal

import pytest

from app.core.config import ConfigurationError, load_settings


def test_load_settings_uses_quantitative_guardrail_defaults() -> None:
    settings = load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"})

    assert settings.data_history_start.isoformat() == "2016-01-01"
    assert settings.benchmark_primary == "000300.SH"
    assert settings.benchmark_secondary == "000001.SH"
    assert settings.default_initial_equity == Decimal("20000.00")
    assert settings.max_investment_ratio == Decimal("0.70")
    assert settings.max_positions == 4
    assert settings.drawdown_warning == Decimal("0.06")
    assert settings.drawdown_stop == Decimal("0.08")
    assert settings.execution_price_mode == "NEXT_OPEN_ADJUSTED"
    assert settings.partial_fill_mode == "FULL_OR_NONE"


def test_load_settings_rejects_invalid_drawdown_ordering() -> None:
    with pytest.raises(ConfigurationError, match="DRAWDOWN_STOP"):
        load_settings(
            {
                "APP_ENV": "test",
                "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
                "DRAWDOWN_WARNING": "0.08",
                "DRAWDOWN_STOP": "0.06",
            }
        )


def test_load_settings_requires_a_secret_outside_development() -> None:
    with pytest.raises(ConfigurationError, match="AUTH_SECRET_KEY"):
        load_settings({"APP_ENV": "production"})


def test_load_settings_requires_an_explicit_environment_and_strong_production_secret() -> None:
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        load_settings({"AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"})
    with pytest.raises(ConfigurationError, match="AUTH_SECRET_KEY"):
        load_settings({"APP_ENV": "production", "AUTH_SECRET_KEY": "short"})
    with pytest.raises(ConfigurationError, match="AUTH_SECRET_KEY"):
        load_settings(
            {"APP_ENV": "production", "AUTH_SECRET_KEY": "development-only-secret-change-me"}
        )
