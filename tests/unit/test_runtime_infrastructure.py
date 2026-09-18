from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool

from app.core.config import ConfigurationError, load_settings
from app.core.errors import DependencyError
from app.infrastructure.db.session import database_runtime, make_engine
from app.jobs.queue import QueueSettings, enqueue, redis_connection
from scripts.run_worker import worker_command


def test_database_runtime_distinguishes_sqlite_and_postgresql() -> None:
    assert database_runtime("sqlite:///:memory:") == "sqlite"
    assert database_runtime("postgresql+psycopg://user:pass@db/money") == "postgresql"


def test_sqlite_in_memory_engine_is_shareable_across_runtime_threads() -> None:
    engine = make_engine("sqlite:///:memory:")

    assert isinstance(engine.pool, StaticPool)
    assert engine.url.get_backend_name() == "sqlite"
    assert engine.url.query == {}


def test_postgresql_engine_enables_connection_health_probes() -> None:
    engine = make_engine("postgresql+psycopg://user:pass@db/money")

    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == 1800


def test_production_rejects_sqlite_runtime() -> None:
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        load_settings(
            {
                "APP_ENV": "production",
                "AUTH_SECRET_KEY": "production-secret-that-is-long-enough-123",
                "DATABASE_URL": "sqlite:///./unsafe.db",
            }
        )


def test_invalid_database_url_is_rejected_during_configuration() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(
            {
                "APP_ENV": "test",
                "AUTH_SECRET_KEY": "test-secret",
                "DATABASE_URL": "mysql+pymysql://user:pass@db/money",
            }
        )


def test_queue_settings_validate_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_PORT", "not-a-port")

    with pytest.raises(ConfigurationError, match="REDIS_PORT"):
        QueueSettings.from_env()


def test_queue_settings_reads_project_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "REDIS_HOST=redis.from-file\n"
        "REDIS_PORT=6380\n"
        "REDIS_PASSWORD=file-password\n"
        "REDIS_DB=3\n"
        "RQ_QUEUE=market-data\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.config._ENV_FILE", env_file)
    for key in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "RQ_QUEUE"):
        monkeypatch.delenv(key, raising=False)

    settings = QueueSettings.from_env()

    assert settings == QueueSettings(
        host="redis.from-file",
        port=6380,
        password="file-password",
        db=3,
        queue_name="market-data",
    )


def test_redis_connection_pings_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRedis:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def ping(self) -> None:
            raise RuntimeError("redis unavailable")

    fake_module = SimpleNamespace(Redis=FakeRedis, exceptions=SimpleNamespace(RedisError=()))
    monkeypatch.setattr("app.jobs.queue.importlib.import_module", lambda name: fake_module)

    with pytest.raises(
        DependencyError, match=r"Redis is unavailable at redis:6379/0"
    ) as error:
        redis_connection(QueueSettings(host="redis", port=6379))

    assert "password" not in str(error.value).lower()


def test_enqueue_translates_broker_write_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQueue:
        def enqueue(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("broker write failed")

    monkeypatch.setattr("app.jobs.queue.rq_queue", lambda _settings: FakeQueue())

    with pytest.raises(DependencyError, match=r"Redis queue is unavailable \(RuntimeError\): broker write failed"):
        enqueue(lambda: None, settings=QueueSettings(), job_id="job-1")


def test_worker_command_uses_validated_queue_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_worker.shutil.which", lambda _name: "rq.exe")

    command = worker_command(
        QueueSettings(host="redis.internal", port=6380, db=2, queue_name="research")
    )
    expected = [
        str(Path(sys.executable).resolve().parent / "rq.exe"),
        "worker",
        "--url",
        "redis://redis.internal:6380/2",
    ]
    assert command[:4] == expected
    assert command[-1] == "research"


def test_worker_command_includes_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_worker.shutil.which", lambda _name: "rq.exe")

    command = worker_command(
        QueueSettings(host="redis.internal", port=6380, password="p@ss word", db=2)
    )
    expected = [
        str(Path(sys.executable).resolve().parent / "rq.exe"),
        "worker",
        "--url",
        "redis://:p%40ss%20word@redis.internal:6380/2",
    ]
    assert command[:4] == expected
    assert command[-1] == "default"


def test_worker_command_uses_simple_worker_on_windows() -> None:
    command = worker_command(QueueSettings())
    if os.name == "nt":
        assert command[command.index("--worker-class") + 1] == "rq.worker.SimpleWorker"
