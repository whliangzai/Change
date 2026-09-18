"""Validated, immutable runtime settings."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from sqlalchemy.engine import make_url


class ConfigurationError(ValueError):
    """Raised when environment configuration violates a safety invariant."""


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _read_env_file() -> dict[str, str]:
    """Read the optional project-root .env without mutating process environment."""
    return {
        key: value
        for key, value in dotenv_values(_ENV_FILE).items()
        if key and value is not None
    }


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
    database_url: str
    database_backend: str = "sqlite"
    job_queue_stale_after_seconds: int = 300
    development_username: str | None = None
    development_password: str | None = None
    # iFinD credentials are deployment secrets.  Keep the token out of repr/log output.
    ifind_enabled: bool = False
    ifind_full_enabled: bool = False
    ifind_base_url: str = "https://quantapi.51ifind.com"
    ifind_refresh_token: str = field(default="", repr=False)
    ifind_daily_run_at: str = "18:30"
    ifind_max_codes_per_request: int = 200
    ifind_max_concurrency: int = 2
    # Tushare is the primary publishable HTTP provider. Token is omitted from repr.
    tushare_enabled: bool = False
    tushare_full_enabled: bool = False
    tushare_token: str = field(default="", repr=False)
    tushare_timeout_seconds: float = 20.0
    tushare_rate_limit_per_minute: int = 120
    tushare_max_codes_per_request: int = 200
    tushare_max_concurrency: int = 2
    tushare_pilot_codes: tuple[str, ...] = ("000001.SZ", "600000.SH", "000300.SH", "000001.SH")
    tushare_daily_run_at: str = "18:30"
    akshare_validation_enabled: bool = True
    akshare_price_relative_tolerance: Decimal = Decimal("0")
    akshare_volume_relative_tolerance: Decimal = Decimal("0")
    akshare_amount_relative_tolerance: Decimal = Decimal("0")


_DEFAULTS: dict[str, str] = {
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
    "DATABASE_URL": "sqlite:///./money-mvp.db",
    "JOB_QUEUE_STALE_AFTER_SECONDS": "300",
    "IFIND_ENABLED": "false",
    "IFIND_FULL_ENABLED": "false",
    "IFIND_BASE_URL": "https://quantapi.51ifind.com",
    "IFIND_DAILY_RUN_AT": "18:30",
    "IFIND_MAX_CODES_PER_REQUEST": "200",
    "IFIND_MAX_CONCURRENCY": "2",
    "TUSHARE_ENABLED": "false",
    "TUSHARE_FULL_ENABLED": "false",
    "TUSHARE_TIMEOUT_SECONDS": "20",
    "TUSHARE_RATE_LIMIT_PER_MINUTE": "120",
    "TUSHARE_MAX_CODES_PER_REQUEST": "200",
    "TUSHARE_MAX_CONCURRENCY": "2",
    "TUSHARE_PILOT_CODES": "000001.SZ,600000.SH,000300.SH,000001.SH",
    "TUSHARE_DAILY_RUN_AT": "18:30",
    "AKSHARE_VALIDATION_ENABLED": "true",
    "AKSHARE_PRICE_RELATIVE_TOLERANCE": "0",
    "AKSHARE_VOLUME_RELATIVE_TOLERANCE": "0",
    "AKSHARE_AMOUNT_RELATIVE_TOLERANCE": "0",
}


def _boolean(values: Mapping[str, str], key: str) -> bool:
    raw = values.get(key, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"{key} must be a boolean")


def _decimal(values: Mapping[str, str], key: str) -> Decimal:
    try:
        value = Decimal(values[key])
    except (InvalidOperation, KeyError) as exc:
        raise ConfigurationError(f"{key} must be a Decimal") from exc
    if not value.is_finite():
        raise ConfigurationError(f"{key} must be finite")
    return value


def _daily_time(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise ConfigurationError(f"{key} must use HH:MM format") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59) or len(hour_text) != 2 or len(minute_text) != 2:
        raise ConfigurationError(f"{key} must use HH:MM format")
    return value


def load_settings(overrides: Mapping[str, str] | None = None) -> Settings:
    """Load settings from .env, process environment, and explicit overrides.

    Precedence is explicit overrides > process environment > project .env.
    """
    file_values = _read_env_file() if overrides is None else {}
    supplied_values = file_values | dict(os.environ) | dict(overrides or {})
    values = _DEFAULTS | supplied_values
    app_env = supplied_values.get("APP_ENV", "").lower()
    if not app_env:
        raise ConfigurationError("APP_ENV must be explicitly configured")
    secret = supplied_values.get("AUTH_SECRET_KEY", "")
    if not secret:
        raise ConfigurationError("AUTH_SECRET_KEY must be configured")
    if app_env not in {"development", "test"} and (
            len(secret) < 32 or secret == "development-only-secret-change-me"
    ):
        raise ConfigurationError(
            "AUTH_SECRET_KEY must be at least 32 characters outside development"
        )
    database_url = values.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigurationError("DATABASE_URL must be configured")
    try:
        database_backend = make_url(database_url).get_backend_name()
    except Exception as exc:
        raise ConfigurationError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
    if database_backend not in {"sqlite", "postgresql"}:
        raise ConfigurationError("DATABASE_URL must use SQLite or PostgreSQL")
    if app_env in {"production", "simulation"} and database_backend != "postgresql":
        raise ConfigurationError("production and simulation runtimes require PostgreSQL")
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
    try:
        job_queue_stale_after_seconds = int(values["JOB_QUEUE_STALE_AFTER_SECONDS"])
    except (KeyError, ValueError):
        raise ConfigurationError("JOB_QUEUE_STALE_AFTER_SECONDS must be an integer") from None
    if not 30 <= job_queue_stale_after_seconds <= 86_400:
        raise ConfigurationError("JOB_QUEUE_STALE_AFTER_SECONDS must be between 30 and 86400")
    if values["EXECUTION_PRICE_MODE"] != "NEXT_OPEN_ADJUSTED":
        raise ConfigurationError("EXECUTION_PRICE_MODE must be NEXT_OPEN_ADJUSTED")
    if values["PARTIAL_FILL_MODE"] != "FULL_OR_NONE":
        raise ConfigurationError("PARTIAL_FILL_MODE must be FULL_OR_NONE")
    ifind_enabled = _boolean(values, "IFIND_ENABLED")
    ifind_full_enabled = _boolean(values, "IFIND_FULL_ENABLED")
    ifind_base_url = values.get("IFIND_BASE_URL", "").strip().rstrip("/")
    parsed_ifind_url = urlparse(ifind_base_url)
    if parsed_ifind_url.scheme != "https" or parsed_ifind_url.hostname != "quantapi.51ifind.com":
        raise ConfigurationError("IFIND_BASE_URL must be https://quantapi.51ifind.com")
    ifind_refresh_token = values.get("IFIND_REFRESH_TOKEN", "").strip()
    if ifind_enabled and not ifind_refresh_token:
        raise ConfigurationError(
            "IFIND_REFRESH_TOKEN must be configured when IFIND_ENABLED is true"
        )
    ifind_daily_run_at = _daily_time(values, "IFIND_DAILY_RUN_AT")
    try:
        ifind_max_codes = int(values.get("IFIND_MAX_CODES_PER_REQUEST", ""))
        ifind_max_concurrency = int(values.get("IFIND_MAX_CONCURRENCY", ""))
    except ValueError:
        raise ConfigurationError(
            "IFIND_MAX_CODES_PER_REQUEST and IFIND_MAX_CONCURRENCY must be integers"
        ) from None
    if not 1 <= ifind_max_codes <= 200:
        raise ConfigurationError("IFIND_MAX_CODES_PER_REQUEST must be between 1 and 200")
    if not 1 <= ifind_max_concurrency <= 32:
        raise ConfigurationError("IFIND_MAX_CONCURRENCY must be between 1 and 32")
    tushare_enabled = _boolean(values, "TUSHARE_ENABLED")
    tushare_full_enabled = _boolean(values, "TUSHARE_FULL_ENABLED")
    tushare_token = values.get("TUSHARE_TOKEN", "").strip()
    if tushare_enabled and not tushare_token:
        raise ConfigurationError("TUSHARE_TOKEN must be configured when TUSHARE_ENABLED is true")
    try:
        tushare_timeout_seconds = float(values["TUSHARE_TIMEOUT_SECONDS"])
        tushare_rate_limit = int(values["TUSHARE_RATE_LIMIT_PER_MINUTE"])
        tushare_max_codes = int(values["TUSHARE_MAX_CODES_PER_REQUEST"])
        tushare_max_concurrency = int(values["TUSHARE_MAX_CONCURRENCY"])
    except (KeyError, ValueError):
        raise ConfigurationError(
            "Tushare timeout, rate limit, batching, and concurrency must be numeric"
        ) from None
    if not 0 < tushare_timeout_seconds <= 300:
        raise ConfigurationError("TUSHARE_TIMEOUT_SECONDS must be between 0 and 300")
    if not 1 <= tushare_rate_limit <= 10_000:
        raise ConfigurationError("TUSHARE_RATE_LIMIT_PER_MINUTE must be between 1 and 10000")
    if not 1 <= tushare_max_codes <= 5000:
        raise ConfigurationError("TUSHARE_MAX_CODES_PER_REQUEST must be between 1 and 5000")
    if not 1 <= tushare_max_concurrency <= 32:
        raise ConfigurationError("TUSHARE_MAX_CONCURRENCY must be between 1 and 32")
    tushare_pilot_codes = tuple(
        code.strip().upper() for code in values["TUSHARE_PILOT_CODES"].split(",") if code.strip()
    )
    if set(tushare_pilot_codes) != {"000001.SZ", "600000.SH", "000300.SH", "000001.SH"}:
        raise ConfigurationError("TUSHARE_PILOT_CODES must contain the four approved pilot codes")
    tushare_daily_run_at = _daily_time(values, "TUSHARE_DAILY_RUN_AT")
    akshare_validation_enabled = _boolean(values, "AKSHARE_VALIDATION_ENABLED")
    tolerances = {
        key: _decimal(values, key)
        for key in (
            "AKSHARE_PRICE_RELATIVE_TOLERANCE",
            "AKSHARE_VOLUME_RELATIVE_TOLERANCE",
            "AKSHARE_AMOUNT_RELATIVE_TOLERANCE",
        )
    }
    if any(value < 0 or value > 1 for value in tolerances.values()):
        raise ConfigurationError("AKShare relative tolerances must be between 0 and 1")
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
        database_url=database_url,
        database_backend=database_backend,
        job_queue_stale_after_seconds=job_queue_stale_after_seconds,
        development_username=(
            values.get("DEVELOPMENT_USERNAME", "").strip() or "admin"
            if app_env == "development"
            else None
        ),
        development_password=(
            values.get("DEVELOPMENT_PASSWORD", "") or "admin" if app_env == "development" else None
        ),
        ifind_enabled=ifind_enabled,
        ifind_full_enabled=ifind_full_enabled,
        ifind_base_url=ifind_base_url,
        ifind_refresh_token=ifind_refresh_token,
        ifind_daily_run_at=ifind_daily_run_at,
        ifind_max_codes_per_request=ifind_max_codes,
        ifind_max_concurrency=ifind_max_concurrency,
        tushare_enabled=tushare_enabled,
        tushare_full_enabled=tushare_full_enabled,
        tushare_token=tushare_token,
        tushare_timeout_seconds=tushare_timeout_seconds,
        tushare_rate_limit_per_minute=tushare_rate_limit,
        tushare_max_codes_per_request=tushare_max_codes,
        tushare_max_concurrency=tushare_max_concurrency,
        tushare_pilot_codes=tushare_pilot_codes,
        tushare_daily_run_at=tushare_daily_run_at,
        akshare_validation_enabled=akshare_validation_enabled,
        akshare_price_relative_tolerance=tolerances["AKSHARE_PRICE_RELATIVE_TOLERANCE"],
        akshare_volume_relative_tolerance=tolerances["AKSHARE_VOLUME_RELATIVE_TOLERANCE"],
        akshare_amount_relative_tolerance=tolerances["AKSHARE_AMOUNT_RELATIVE_TOLERANCE"],
    )
