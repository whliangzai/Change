from __future__ import annotations

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


def test_redis_connection_pings_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRedis:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def ping(self) -> None:
            raise RuntimeError("redis unavailable")

    fake_module = SimpleNamespace(Redis=FakeRedis, exceptions=SimpleNamespace(RedisError=()))
    monkeypatch.setattr("app.jobs.queue.importlib.import_module", lambda name: fake_module)

    with pytest.raises(DependencyError, match="Redis"):
        redis_connection(QueueSettings(host="redis", port=6379))


def test_enqueue_translates_broker_write_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQueue:
        def enqueue(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("broker write failed")

    monkeypatch.setattr("app.jobs.queue.rq_queue", lambda _settings: FakeQueue())

    with pytest.raises(DependencyError, match="queue"):
        enqueue(lambda: None, settings=QueueSettings(), job_id="job-1")


def test_worker_command_uses_validated_queue_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_worker.shutil.which", lambda _name: "rq.exe")

    assert worker_command(
        QueueSettings(host="redis.internal", port=6380, db=2, queue_name="research")
    ) == [
        "rq.exe",
        "worker",
        "--url",
        "redis://redis.internal:6380/2",
        "research",
    ]
