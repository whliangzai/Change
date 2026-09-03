"""Validated, immutable runtime settings."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class ConfigurationError(ValueError):
    """Raised when environment configuration violates a safety invariant."""


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    auth_secret_key: str
    data_history_start: date
    benchmark_primary: str
    benchmark_secondary: str
    default_initial_equity: Decimal
    max_investment_ratio: Decimal
    max_positions: int
    drawdown_warning: Decimal
    drawdown_stop: Decimal
    execution_price_mode: str
    partial_fill_mode: str


_DEFAULTS: dict[str, str] = {
    "APP_ENV": "development",
    "AUTH_SECRET_KEY": "development-only-secret-change-me",
    "DATA_HISTORY_START": "2016-01-01",
    "BENCHMARK_PRIMARY": "000300.SH",
    "BENCHMARK_SECONDARY": "000001.SH",
    "DEFAULT_INITIAL_EQUITY": "20000.00",
    "MAX_INVESTMENT_RATIO": "0.70",
    "MAX_POSITIONS": "4",
    "DRAWDOWN_WARNING": "0.06",
    "DRAWDOWN_STOP": "0.08",
    "EXECUTION_PRICE_MODE": "NEXT_OPEN_ADJUSTED",
    "PARTIAL_FILL_MODE": "FULL_OR_NONE",
}


def _decimal(values: Mapping[str, str], key: str) -> Decimal:
    try:
        value = Decimal(values[key])
    except (InvalidOperation, KeyError) as exc:
        raise ConfigurationError(f"{key} must be a Decimal") from exc
    if not value.is_finite():
        raise ConfigurationError(f"{key} must be finite")
    return value


def load_settings(overrides: Mapping[str, str] | None = None) -> Settings:
    """Load settings from the process environment with explicit test overrides."""
    supplied_values = dict(os.environ) | dict(overrides or {})
    values = _DEFAULTS | supplied_values
    app_env = values["APP_ENV"].lower()
    secret = supplied_values.get(
        "AUTH_SECRET_KEY", _DEFAULTS["AUTH_SECRET_KEY"] if app_env == "development" else ""
    )
    if app_env != "development" and len(secret) < 32:
        raise ConfigurationError(
            "AUTH_SECRET_KEY must be at least 32 characters outside development"
        )
    try:
        data_history_start = date.fromisoformat(values["DATA_HISTORY_START"])
        max_positions = int(values["MAX_POSITIONS"])
    except (KeyError, ValueError) as exc:
        raise ConfigurationError("DATA_HISTORY_START and MAX_POSITIONS must be valid") from exc
    warning = _decimal(values, "DRAWDOWN_WARNING")
    stop = _decimal(values, "DRAWDOWN_STOP")
    investment_ratio = _decimal(values, "MAX_INVESTMENT_RATIO")
    initial_equity = _decimal(values, "DEFAULT_INITIAL_EQUITY")
    if not Decimal("0") < warning < stop < Decimal("1"):
        raise ConfigurationError("DRAWDOWN_STOP must be greater than DRAWDOWN_WARNING and below 1")
    if not Decimal("0") < investment_ratio <= Decimal("1"):
        raise ConfigurationError("MAX_INVESTMENT_RATIO must be in (0, 1]")
    if initial_equity <= Decimal("0") or max_positions < 1:
        raise ConfigurationError("DEFAULT_INITIAL_EQUITY and MAX_POSITIONS must be positive")
    if values["EXECUTION_PRICE_MODE"] != "NEXT_OPEN_ADJUSTED":
        raise ConfigurationError("EXECUTION_PRICE_MODE must be NEXT_OPEN_ADJUSTED")
    if values["PARTIAL_FILL_MODE"] != "FULL_OR_NONE":
        raise ConfigurationError("PARTIAL_FILL_MODE must be FULL_OR_NONE")
    return Settings(
        app_env=app_env,
        auth_secret_key=secret,
        data_history_start=data_history_start,
        benchmark_primary=values["BENCHMARK_PRIMARY"],
        benchmark_secondary=values["BENCHMARK_SECONDARY"],
        default_initial_equity=initial_equity,
        max_investment_ratio=investment_ratio,
        max_positions=max_positions,
        drawdown_warning=warning,
        drawdown_stop=stop,
        execution_price_mode=values["EXECUTION_PRICE_MODE"],
        partial_fill_mode=values["PARTIAL_FILL_MODE"],
    )
