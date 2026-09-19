from decimal import Decimal

import pytest

from app.core.config import ConfigurationError, load_settings


def test_load_settings_uses_quantitative_guardrail_defaults() -> None:
    settings = load_settings(
        {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
    )

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
    assert settings.ifind_full_enabled is False
    assert settings.job_queue_stale_after_seconds == 300
    assert settings.provider_import_execution == "rq"


def test_background_provider_execution_is_limited_to_local_environments() -> None:
    for app_env in ("development", "test"):
        settings = load_settings(
            {
                "APP_ENV": app_env,
                "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
                "PROVIDER_IMPORT_EXECUTION": "background",
            }
        )
        assert settings.provider_import_execution == "background"

    for app_env in ("simulation", "production"):
        with pytest.raises(ConfigurationError, match="only allowed"):
            load_settings(
                {
                    "APP_ENV": app_env,
                    "AUTH_SECRET_KEY": "production-secret-that-is-long-enough",
                    "DATABASE_URL": "postgresql://localhost/money",
                    "PROVIDER_IMPORT_EXECUTION": "background",
                }
            )


def test_invalid_provider_execution_mode_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="must be rq or background"):
        load_settings(
            {
                "APP_ENV": "test",
                "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
                "PROVIDER_IMPORT_EXECUTION": "inline",
            }
        )


def test_load_settings_reads_project_env_file_with_environment_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\n"
        "AUTH_SECRET_KEY=file-secret\n"
        "TUSHARE_ENABLED=true\n"
        "TUSHARE_TOKEN=file-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.config._ENV_FILE", env_file)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    monkeypatch.delenv("TUSHARE_ENABLED", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    from_file = load_settings()
    assert from_file.tushare_enabled is True
    assert from_file.tushare_token == "file-token"

    monkeypatch.setenv("TUSHARE_TOKEN", "process-token")
    from_process = load_settings()
    assert from_process.tushare_token == "process-token"

    overridden = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "override-secret",
            "TUSHARE_ENABLED": "false",
        }
    )
    assert overridden.tushare_enabled is False
    assert overridden.tushare_token == "process-token"


def test_ifind_full_gate_is_explicit_and_defaults_closed() -> None:
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "IFIND_ENABLED": "true",
            "IFIND_REFRESH_TOKEN": "deployment-secret",
            "IFIND_FULL_ENABLED": "true",
        }
    )

    assert settings.ifind_full_enabled is True


def test_development_account_is_optional_and_supplied_only_by_environment() -> None:
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DEVELOPMENT_USERNAME": "researcher",
            "DEVELOPMENT_PASSWORD": "local-test-password",
        }
    )

    assert settings.development_username == "researcher"
    assert settings.development_password == "local-test-password"


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
